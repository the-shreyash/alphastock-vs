# StockAssist AI
## Changelog

This file records documentation-system versions and, from v1.0 launch onward, product release notes. Documentation versions apply to the `.claude/` documentation set as a whole.

---

# Sprint PH3.1 — Test Infrastructure & Test Stabilization — 2026-08-09

**PHASE 3 OPENS. Decision: CERTIFIED.** Full report:
`docs/testing/PH3.1_TEST_CERTIFICATION.md`. Developer reference:
`docs/testing/TEST_ARCHITECTURE.md`.

**The default test command works now.** `pytest` reports **1,035 passed, 0
failed, 0 errors** in ~2m20s, on a machine with no server, no database, no
credentials and no network. Before this sprint the same command reported **47
failures and 51 errors**, because the live-server suites were collected by
default and had nothing to connect to. The signal was not weak; it was absent,
and everyone working in this repository had learned that red is normal.

**The larger finding is what the "hermetic" suite was actually doing.** The
charter assumed hermeticity and asked only for the live suites to be marked.
Socket instrumentation built for this sprint found **three tests in the default
suite opening live TLS connections on every single run** — `api.anthropic.com`,
Google's Generative Language API, and Yahoo Finance — **authenticated with the
developer's real production API keys.** The mechanism is one line:
`server.py` calls `load_dotenv(backend/.env, override=True)` at import time, and
`tests/conftest.py` imports `server`. Nobody could have noticed from the output:
those call sites are wrapped in broad exception handlers — correctly, since a
provider outage must not take the API down — so a live call and a mocked one
produce the same green tick.

It is now closed three independent ways, and measured rather than asserted:

- **`backend/tests/_testenv.py`** — installs a fixed synthetic environment
  *before* `server` is imported, sets `PYTHON_DOTENV_DISABLED=1` (python-dotenv's
  own supported kill switch, so `load_dotenv` becomes a no-op rather than
  something to monkeypatch), sets `APP_ENV=testing`, and blanks every
  third-party credential. Values are **overwritten, not defaulted** — `setdefault`
  would let a real key exported in the developer's shell walk straight past the
  guard, which is the exact hole this closes.
- **`backend/tests/_netguard.py`** — an autouse guard that raises on any
  non-loopback `socket.connect`. Patched at the socket layer rather than
  per-client because the three escapes came through three *different* HTTP
  clients (`aiohttp`, `httpx`, `requests` via yfinance); a per-client allowlist
  goes stale the moment someone adds a fourth. `NetworkAccessBlocked` subclasses
  `OSError` so application code takes its normal offline path and the test fails
  on a wrong assertion, which is diagnosable.
- **Blank credentials**, so every `*_configured()` check reads false and routes
  take their documented offline branch.

Result: **zero outbound connections** across the full default run, and runtime
down from 202 s to 139 s because three tests had been waiting on real API
round-trips.

**Two genuine implementation defects, both in `backend/security/secrets.py`.**
`app_env()` and `get()` used the idiom `(environ or os.environ)`. An empty
mapping is falsy, so `secrets.app_env({})` — a caller explicitly asking "what
does this resolve to with nothing configured?" — was answered with **the host's
live configuration**. In a security-configuration reader that is wrong in the
most dangerous direction. Fixed to an explicit `is None` sentinel in both
places. It had survived every prior review because the test that catches it
(`test_app_env_defaults_to_development`) was itself running in a process that
had loaded the developer's `.env`, which happens not to set `APP_ENV` — so the
assertion passed for the wrong reason. **This is the same pattern PH2.12
recorded** — a check and the thing it checks sharing an assumption — one level
further up the stack.

**The chartered stale test was already fixed.**
`test_run_cycle_trails_and_books_targets` was investigated against
`services/trading_engine.py:346` and found repaired by a prior sprint, with the
reason documented inline and the exact-equality assertion intact — not
weakened. Recorded as classification B rather than silently ticked off.

**Test data and credential hygiene.** The pair `admin@alphapartner.com` /
`admin123` was a literal or a default in **five** of the six live-server files
(and is the credential `IMPLEMENTATION_REPORT.md` logged as Critical finding
C1). Two files located the deployment by scraping `/app/frontend/.env` and then
walking up the source tree — making the *target of the test* a property of the
machine running it. Both are gone; `backend/tests/_live.py` owns live
configuration, and `TEST_ADMIN_EMAIL` / `TEST_ADMIN_PASSWORD` have no defaults.
`test_phase7.py::TestWhatsAppLive::test_send_test_message_via_twilio` — the one
test in the repository that **sends a real, billable WhatsApp message** — now
requires a second explicit opt-in, `ALLOW_LIVE_WHATSAPP_SEND=1`.

**Live suites skip honestly instead of failing.**
`conftest.py::_require_live_server` probes the deployment once per session and
skips `live` tests when nothing answers: 95 skipped in 0.28 s, versus 47
failures and 51 errors over ~3 minutes. **`REQUIRE_LIVE_BACKEND=1` turns those
skips into failures** — and **the PH2.6 integration job must set it**, because
a stack that failed to boot skipping its way to a green tick is a worse outcome
than the red one this replaced. The same switch governs missing credentials.

**Marker taxonomy**, registered in `backend/pyproject.toml`:
`integration`, `live`, `e2e`, `security`, `slow`, `requires_db`,
`requires_redis`, `allow_network`. The load-bearing ones are applied
**mechanically** from filename tuples in `conftest.py`, never by hand — a
decorator on 452 security tests would be missing from some of them within a
sprint, and `pytest -m security` would then quietly under-report the regression
surface. There is deliberately **no `unit` marker**: hand-applying it to ~1,000
tests to make `-m unit` mean something is a job that would be done badly, and a
marker missing from most of the tests it describes is worse than none.

**Files**

- **`backend/tests/_testenv.py`** (new) — deterministic test environment.
- **`backend/tests/_netguard.py`** (new) — outbound-network guard.
- **`backend/tests/_live.py`** (new) — live-suite configuration; one `BASE_URL`,
  one `admin_login()`, credentials from env with no defaults.
- **`backend/tests/test_api_contract.py`** (new) — **19 hermetic API-contract
  tests** converted from the live suite: market overview, stock
  universe/search/detail, the 404-vs-503 distinction, top picks, portfolio
  summary, notification ownership filtering, SIP calculator. Deliberately
  includes the **degraded** branches (`available: false`, empty live fetch not
  persisted) that production hits during a provider outage and that a live test
  cannot trigger on demand. **Every assertion was mutation-checked** — five
  representative assertions individually inverted, all five detected — because
  a new test file that passes on the first run deserves suspicion.
- **`backend/tests/conftest.py`** — isolation guards, mechanical marker
  application, live-server gating.
- **`backend/pyproject.toml`** — marker taxonomy, `-m "not integration"` in
  `addopts`, `[tool.coverage.*]`.
- **`backend/requirements-dev.txt`** — `pytest-cov==7.0.0`, `coverage==7.15.4`.
  Dev-only; `requirements.txt` is untouched and the runtime image is unchanged.
- **`backend/security/secrets.py`** — the two `(environ or os.environ)` fixes.
- **`backend/tests/test_backend.py` → `test_backend_live.py`** — not cosmetic:
  the old name read as "the backend tests", which is how it came to be run by
  default and how the default command came to be permanently red.
- **`backend/tests/test_phase{2,4,5,6,7}.py`** — credentials, base URL, Twilio
  opt-in.
- **`backend/tests/test_backup_restore.py`** — `TestRetention` marked `slow`
  (43 s of real sleeps; not a hidden race — the pruner sorts by whole-second
  mtime, so the fixture genuinely has to space artifacts out).
- **`backend/tests/test_phase8.py`** — deleted; zero bytes since 2026-06-09.
- **`.github/workflows/backend-ci.yml`** — the test job no longer sets
  `APP_ENV`/`MONGO_URL`/`DB_NAME`/`JWT_SECRET`. `_testenv.py` overwrites, so
  those values were already being ignored; leaving them would imply CI and a
  laptop run different configurations. They now provably run the same one.
- **`docs/testing/`** (new) — `TEST_ARCHITECTURE.md`,
  `PH3.1_TEST_CERTIFICATION.md`, `README.md`; indexed in `docs/README.md`.

**Coverage baseline** (`pytest --cov`, statements, application code only):
**59.2%** — `security/` 94.8%, `observability/` 95.8%, `infrastructure/` 82.4%,
`trading_engine` 82.0%, `brokers/` 56.9%, `server.py` 51.9%,
`market_engine/` 46.5%, `services/` other 42.4%. Recorded as 59.2% rather than
the 72% that including `tests/` in the denominator produces — the flattering
figure being the meaningless one, since test files are ~100% covered by
construction. No `fail_under`: a threshold invented alongside the first
measurement is a number pulled from the air, and the first person it blocks
will lower it. PH3.11 sets one from trend data.

A configuration trap worth recording: coverage's `source` resolves package names
and directories and **silently ignores a plain file path**. The first attempt
listed `"server.py"` explicitly, which dropped the largest module (2,897
statements) out of the denominator and reported a number ~8 points too high.
Corrected to `source = ["."]` plus an omit list, so a new package is included by
default rather than forgotten.

**Regression** — `pytest -m security`: **452 passed** (OAuth, cookies, CORS,
JWT, sessions, password policy, rate limiting, CSRF, headers, RBAC, identifier
validation, audit, secret loading, recovery). PH2 infrastructure suites all
green (observability 123, Redis 50, backup/restore 39, DR 43). Blocking flake8
gate clean; `compileall` clean; application imports on runtime deps alone (204
routed endpoints); `mypy` on `security/` unchanged at the 2 pre-existing
`bool(x) and ...` false positives documented in `pyproject.toml`.

**Not delivered, with owners:** no CI integration job (PH2.6), no frontend tests
(PH3.3), no branch coverage or coverage gate (PH3.11). And `FakeDB` remains an
operator-subset double — a query using an unmodelled Mongo operator behaves
differently under test than in production, which is precisely why a green
hermetic suite must not become the argument for dropping the integration layer.

---

# Sprint PH2.12 — Infrastructure Certification & Release Readiness — 2026-08-09

**PHASE 2 EXIT GATE. Decision: CONDITIONALLY CERTIFIED. Infrastructure score
8.0 / 10** (from ~6.4 post-PH1). Full report: `docs/infrastructure/PH2_CERTIFICATION.md`.

**This is the first PH2 sprint with a working Docker daemon.** Every sprint from
PH2.7 onward carried the same limitation — *no Docker daemon in the sprint
environment* — so the container stack, compose topology, backup transport, DR
verifier and rollback script had all shipped on hermetic tests and careful
reading. Certifying against a live stack (Docker 29.4.0, fresh volumes, real
secrets) found **one Critical and two High defects that no amount of further
reading would have found**, and confirmed the rest of the infrastructure is
genuinely strong.

**CRITICAL — the rollback did not roll back, and reported success.**
`deploy_rollback.sh` rewrote `BACKEND_IMAGE_TAG` in `.env`, ran compose, recreated
**nothing**, and printed `rollback verified` while the bad release kept serving.
Root cause: `scripts/backup/lib.sh::bk_load_env_file` **exports every key it
parses from `.env`**, so the tag being rolled *away from* was already in the
process environment — and Compose ranks shell variables **above** the `.env` file.
The script's atomic file rewrite was silently outranked by its own config loader.
Verification then passed because it checked *health*, and the release being rolled
away from was perfectly healthy — it was serving wrong behaviour, not failing a
probe, which is the normal case for a rollback. Measured live: `.env` said `cert`,
the container ran `v2-bad`, the app reported `2.13.0-badrelease`, and the script
said verified. **Fixed** by passing the tag on the compose command itself and by
asserting the *running build* (not just health) before declaring success — on
mismatch it now fails, writes `FAILED rollback` to the ledger, and tells the
operator not to close the incident. Post-fix drill: `Recreated`, 10 s,
independently confirmed serving `2.12.0-cert`.

**HIGH — the blocking CI lint gate has been red on every run since PH2.4.** CI
builds its virtualenv at `backend/.venv-ci`; `.flake8` excluded `venv` and
`.venv`, and flake8 matches on the path **basename**, so `.venv-ci` never matched.
CI linted its own `site-packages`, where libraries legitimately trip F811. It
passes locally because the developer venv is named `venv`. A blocking gate that is
always red for reasons outside the repository is a gate a team learns to ignore.
**Fixed**; verified with a controlled before/after (`EXIT=1` → `EXIT=0`, with a
control proving the gate still catches a real F821).

**HIGH — the DR running-build probe could never pass.** `dr_verify.sh` parsed
`"app_version"`/`"vcs_ref"` from `/api/diagnostics`; the endpoint has always
returned them nested as `build.version`/`build.revision`. The check could only
SKIP — or, with `--expect-version`, **FAIL a healthy correctly-deployed stack while
blaming `DR_OPS_TOKEN`**. The hermetic stub emitted the same wrong shape, so the
suite agreed with the bug. **Fixed** in both; the stub now mirrors the real payload
including a `process.python_version` decoy the parser must not match.

**Verified live (evidence, not assertion):** image **423 MB** (PH2.1 shipped
1.03 GB; better than PH2.8's ~650 MB projection), non-root uid 10001, `/app`
unwritable, no pip, no secrets in layers or filesystem; stack healthy in **8 s**
from fresh volumes, graceful shutdown **exit 0** on all three, data intact across
a full `down`/`up`; `data` network `internal:true` with **no** published database
ports and both datastores refusing unauthenticated access; **fail-closed config**
rejected 5 of 6 bad configurations before startup (including a placeholder API key
— which caught the certifier's own stand-in credential); **zero secret leakage**
grepping four *real* live secrets across stdout and file sinks;
liveness/readiness correctly split (Mongo down → `/live` 200, `/ready` 503; Redis
down → app stays `ready`); metrics token-gated with 20+ families; **backup/restore
drilled in `docker` mode — closing PH2.9's L6** — with a **destructive** test
(3 collections dropped → full recovery, 16 matched); `dr_verify --level full`
**12/12**; **1014 hermetic tests pass**; frontend build clean.

**Not fixed, deliberately — documented as required actions:** 6 CVEs in *runtime*
dependencies (`cryptography` 48.0.1, `aiohttp` 3.14.1) — a `cryptography` major
bump touches broker-token Fernet encryption and does not belong in a certification
sprint; npm high-severity advisories; **no off-host backup copy** (leaves R7,
complete server loss, unexecutable); no CD or image registry; no frontend
production image; **and no alerting at all** — detection is entirely manual, which
per PH2.10's own RTO decomposition dominates recovery time and makes the measured
sub-15-second mechanical RTO theoretical.

**Files changed (4, all remediation — no feature work):** `backend/.flake8`,
`scripts/dr/dr_verify.sh`, `scripts/dr/deploy_rollback.sh`,
`backend/tests/test_disaster_recovery.py` (DR suite 41 → **43**; both new tests
proven to fail without the fix by reverting it and re-running). No trading logic,
AI logic, product functionality or architecture was touched. Machine left as
found: stack torn down, volumes removed, certification images deleted, and the
developer `.env` restored **byte-identical** (sha256 verified).

**Engineering lesson recorded as PH3.1's charter:** both the Critical and one High
survived review because their hermetic tests stubbed a system boundary and then
agreed with the implementation rather than the contract. When a probe and its test
share an assumption, only the real system can settle it.

---

# Sprint PH2.10 — Disaster Recovery & Business Continuity — 2026-08-05

**PH2.9 made the data recoverable. This sprint makes the *platform* recoverable,
and makes the recovery provable. Added `docs/operations/DISASTER_RECOVERY.md`
(ten runbooks R1–R10, recovery objectives decomposed phase by phase, seven named
assumptions, severity and escalation, a drill schedule, a pre-disaster
checklist, nine limitations), `docs/runbooks/POSTMORTEM_TEMPLATE.md`, and two
executable scripts: `scripts/dr/dr_verify.sh` — four-layer diagnosis *and*
post-recovery verification — and `scripts/dr/deploy_rollback.sh` — a deployment
ledger and a rollback that refuses to start unless it can finish. Measured
against the live database: restore of 21 collections 4.48 s with 21/21 matched,
full verification 1.10 s, configuration recovery 0.17 s for 14 files. 41
hermetic tests; the two three-line stubs `docs/operations/runbooks.md` and
`incident-response.md` finally have content.**

> **Design note — the RTO is a human number, not a machine number.** The
> instinct on a recovery sprint is to make the restore faster. So the four-hour
> RTO was decomposed phase by phase (§4.2) before anything was optimised, and
> the mechanical work — fetch, decrypt, restore, verify — came to **under five
> minutes**, while *detection* came to **0–30 minutes** and provisioning to
> 30–60. Making `mongorestore` twice as fast would improve the RTO by roughly a
> second. Alerting would improve it by half an hour. That is why this sprint
> shipped no performance work at all, and why the sprint report recommends
> roadmap PH2.10's alerting next rather than anything in this document.

> **Design note — a diagnostic is not a test suite, and must not stop early.**
> `dr_verify.sh` runs every check even after one fails, and reports SKIP for
> checks whose *prerequisite* failed rather than a second, misleading failure.
> During a recovery the operator needs the SHAPE of the failure — "containers
> up, Mongo fine, Redis unreachable" is a different incident from "nothing is
> running" — and obtaining that shape one round-trip at a time, re-running after
> each fix, is how a fifteen-minute recovery becomes an hour. The same tool is
> deliberately used for diagnosis in step 1 and verification in the last step,
> so the command that told you what broke is the command that tells you it is
> fixed.

> **Design note — an empty database passes every check that is easy to write.**
> Containers running, health endpoint 200, compose file valid: a stack that was
> restored without its data satisfies all of them, and the failure surfaces
> hours later as user reports. So "the database has collections" is a hard
> failure in layer 3, and `--expect-manifest` compares per-collection counts
> against the baseline captured at dump time — because **`mongorestore` exits 0
> on a restore that moved nothing**. The comparison was proven non-vacuous by
> inserting one document into one collection and watching it fail
> (`MISMATCH admin_audit_logs expected=7 actual=8`), then removing it and
> watching it pass.

> **Design note — "roll back the deployment" is one sentence and four facts.**
> Which version is running; which was running before; **is that image still on
> this host**; did the rollback take effect. With no registry and no CD
> (PH2.7b), nothing in this deployment answered any of them: the tag lives in a
> hand-edited `.env` and `docker compose up -d` with an unchanged tag is a
> silent no-op. `deploy_rollback.sh` answers all four — ledger, precondition,
> atomic apply, `--expect-version` — and the precondition is the load-bearing
> one: it refuses to stop the running version until the replacement image is
> confirmed present, so a pruned image is discovered *now*, with the current
> version still serving traffic, instead of after the backend has been recreated
> against an image that is not there. When the rollback target is also
> unhealthy, it reverts automatically: a rollback to a second broken version is
> the worst of the three outcomes, because it spends the operator's remaining
> confidence in the mechanism.

> **Honest limitation, stated where it hurts rather than in a footnote.** The
> off-host backup copy is still documented and not implemented (PH2.9 L2), which
> means **R7 — complete server loss — is not executable today**. Every other
> limitation in this sprint is a degradation; that one is a total-loss scenario,
> and it is one `rclone` line plus a quarterly fetch drill away from being
> closed. It is now the first item in §14's ordered improvement list, ahead of
> alerting, a registry, and point-in-time recovery.

---

# Sprint PH2.9 — Production Backup & Restore — 2026-08-04

**The system gets its first way to lose data and get it back. Everything the
product cannot recreate — users, portfolios, trades, the journal, sessions, the
security audit trail — existed in exactly one place: a single Docker named
volume on a single host, which survives `docker compose down` and nothing else.
This sprint adds `scripts/backup/` (six files, one shared library): encrypted,
checksummed, self-describing MongoDB backups on a grandfather-father-son
rotation; three graduated verification levels ending in a real restore into a
scratch database; a restore path that verifies before it writes and again after;
an encrypted archive of the secret material without which a restored database is
useless; and an upload-storage path that exists before uploads ship. Redis is
deliberately NOT backed up, and §6 of the documentation argues why. A full drill
was executed against a live MongoDB 8.0 — 205 000 documents, 26.3 MB, backup
2.06 s, restore 3.51 s, 13.2 : 1 compression, indexes and document contents
verified identical.**

> **Design note — the only thing that distinguishes a backup from a file is a
> restore.** The overwhelming majority of backup failures are not "the job did
> not run". They are "the job ran every night for fourteen months and produced
> files that cannot be restored" — a wrong credential that dumped an empty
> database, a passphrase rotated without re-keying, a full disk that truncated
> every artifact, a missing `docker exec -T` that CRLF-mangled a binary stream.
> Every one of those produces a file of plausible size with a recent mtime, and
> a monitoring check that asks "was a file written?" reports green through all of
> them. So verification here is not a bolt-on: `checksum` (0.12 s) proves the
> bytes have not changed, `structural` (0.31 s) decrypts and runs the entire
> payload through gzip's CRC then confirms the mongodump archive magic — and
> **runs automatically after every backup** — and `drill` (~5 s) restores into
> `<db>__drill_<timestamp>` and compares per-collection document counts against
> a baseline captured at dump time. That baseline is the load-bearing field in
> the manifest, because `mongorestore` exits 0 on a restore that moved nothing.

## Added

- **`scripts/backup/lib.sh`** — the shared library the other five source. One
  definition of encryption, checksums, manifests, retention, the Mongo transport
  and the destructive-action guard, so the encrypt path and the decrypt path
  cannot drift apart. Bash 3.2 compatible on purpose (macOS ships 3.2; a script
  that only works on the production shell is one whose behaviour is first
  observed during an incident).
- **`scripts/backup/backup_mongo.sh`** — `mongodump --archive --gzip` streamed
  through AES-256 straight to disk, so no plaintext copy of the database ever
  exists on a filesystem. Writes to `.partial`, checksums, renames, **checksums
  again** (`mv` across a filesystem is a copy, and a copy is where a full disk
  truncates silently), writes a manifest, then prunes — **last, and only on
  success**, because pruning first and failing to produce the replacement is how
  a retention policy becomes a data-loss policy.
- **`scripts/backup/verify_backup.sh`** — the three levels above, `--latest` and
  `--all` selectors. The drill restores via `--nsFrom`/`--nsTo` into a scratch
  database, making it non-destructive *by construction* — which is what makes it
  safe to run monthly, and a drill you actually run beats a perfect drill you do
  not.
- **`scripts/backup/restore_mongo.sh`** — verify-before-write (the one
  unrecoverable ordering mistake is dropping a live collection and *then*
  discovering the archive is corrupt), merge-by-default rather than replace,
  typed confirmation rather than `y/N` (at 03:00, "y" is muscle memory), and a
  post-restore count comparison against the manifest.
- **`scripts/backup/backup_config.sh`** — encrypted archive of `secrets/` and the
  `.env` family. **Encryption is mandatory here with no development exemption**:
  the archive is 100 % credential material, so there is no environment in which
  plaintext is the right default and therefore no flag for it. Excludes anything
  git already tracks and records the git commit instead, so a recovery checks out
  the revision the secrets were captured against.
- **`scripts/backup/backup_uploads.sh`** — Docker-volume or host-path tarball
  plus restore. Written now, while `backend_uploads` is still declared-but-unmounted,
  because the first day of a new data store is exactly when nobody remembers to
  add it to the backup rotation.
- **`docs/operations/BACKUP_AND_RESTORE.md`** — architecture, storage rules,
  retention reasoning, encryption decision, the verification cost curve, the
  measured results, the ordered restore checklist, secret recovery and the
  recursive-dependency trap, the cron schedule, RPO/RTO, eight disaster
  scenarios, eight known limitations, and a configuration reference.
- **`backend/tests/test_backup_restore.py`** — 39 hermetic tests driving the real
  scripts against stubbed mongo tools. They assert the properties whose failure
  is silent and delayed: empty artifacts are never published, the manifest hash
  describes the *published* file, encrypted artifacts round-trip through the exact
  decrypt path the restore uses, production refuses plaintext, corruption and a
  wrong passphrase are both detected, retention never crosses tiers and never
  touches a file it did not create, a failed dump leaves every previous backup
  intact, and the `.env` reader parses rather than `source`s.

## Decisions

- **Redis is not backed up.** Everything in it is either reconstructible market-data
  cache or in-flight Pub/Sub; sessions, rate limits and the audit log are in
  MongoDB precisely so Redis can stay disposable. PH2.7's AOF is a **warm-start
  optimisation, not a backup** — it exists so a restart does not send every replica
  to re-fetch the quote universe from rate-limited providers at once. Recovery
  procedure: start Redis empty. Documented with a monthly tripwire (a key with no
  TTL is a candidate for something meant to last).
- **One encryption path, not two.** OpenSSL AES-256-CBC/PBKDF2-600000, no GPG
  fallback. The encrypt path runs nightly, the decrypt path runs during an
  outage; a two-tool scheme means the restore is where you discover the recovery
  host has only one of them. Honest limitation recorded: CBC is not
  authenticated.
- **Count-based retention, deliberately diverging from PH2.6's age-first log
  retention.** Logs carry a wall-clock legal commitment; backups carry a coverage
  commitment, and coverage is a count. Age-first would silently reduce seven
  restore points to five in a week when the job failed twice, while every rule
  still passed.
- **Backup settings stay out of `backend/security/secrets.py`.** That registry is
  the application's configuration surface, validated fail-closed at boot and
  mirrored into `.env.example`. `BACKUP_ROOT` is a host-operations setting the app
  never reads; registering it would imply a validation that does not exist and
  blur a boundary that is currently clean.

## Fixed

- **A failing `git status` aborted the entire configuration backup.** Under
  `set -euo pipefail`, `GIT_DIRTY="$(git status --porcelain | head -n 1)"` exits
  128 when the source is not a git checkout, killing the script before it wrote
  anything — with no output at all, because the failure happened during an
  assignment. Provenance is a nice-to-have; it must never be the reason the
  credentials did not get backed up. Found by the test suite.
- **An empty dump was publishable whenever encryption was on.** `openssl enc`
  turns zero bytes of input into a ~32-byte file, so the "refuse an empty
  artifact" check — which is the guard against a mid-dump authentication failure
  — was satisfied by an artifact containing nothing. Post-write structural
  verification is now load-bearing rather than advisory, and a backup that fails
  it is renamed `*.rejected`: outside every glob, so `--latest` cannot select it
  and retention does not count it, but still on disk, because the file is the
  evidence for why the backup failed. Found by the test suite.
- **The working directory was deleted the instant it was created.** `bk_workdir`
  was lazily creating a `mktemp -d` and registering its cleanup trap — but it was
  called from inside `$( … )`, and bash fires EXIT traps when a subshell exits.
  The caller received a path to nothing. Creation now happens once in the main
  shell (`bk_init_workdir`, called from `bk_load_config`).
- **A credential file could be left inside the mongo container.** The container-side
  tools config was staged lazily by `bk_mongo_tool`, which runs as a pipeline
  element — i.e. in a subshell, where the assignment recording the file's path is
  invisible to the parent's cleanup. `bk_prepare_mongo` now stages it eagerly from
  the main shell.

## Verification

- 39/39 new tests pass; `flake8` clean on both the blocking and the full pass.
- Live drill against MongoDB 8.0.13 with the real `alpha_stock_db` (21
  collections): **21/21 collections matched**, scratch database dropped, no
  residue. Wrong passphrase, corrupted artifact, corrupted-artifact-with-rewritten-manifest,
  and unattended restore into a populated database all correctly **rejected**.
- Scale drill (205 000 documents / 26.3 MB): backup **2.06 s** → **1.99 MB**
  artifact (13.2 : 1); restore **3.51 s**; secondary index and sampled document
  contents identical to source.

## Known limitations

L1 no point-in-time recovery (standalone mongod is per-collection consistent
only; the fix is a single-node replica set + `--oplog`) · L2 off-host copy is
documented, not implemented · L3 AES-CBC is unauthenticated · L4 backup failure
is not alerted (PH2.10) · L5 the drill is cron, not CI (CI has no MongoDB) ·
L6 `BACKUP_MODE=docker` is unverified — no Docker daemon in the sprint
environment; every measurement was taken in `direct` mode against a real MongoDB,
and the transports differ only in how the tools are invoked · L7 uploads have no
data and so no executed Docker-volume drill · L8 `docker compose down -v` still
destroys the local volumes. Full detail in `docs/operations/BACKUP_AND_RESTORE.md` §14.

---

# Sprint PH2.8 — Production Configuration & Environment Optimization — 2026-07-24

**Configuration stops being scattered and the runtime stops carrying dead weight.
The configuration architecture was already centralized in
`backend/security/secrets.py` — one registry-driven loader and validator that
fails closed — so this sprint consolidated the model rather than rebuilding it:
documented the source precedence and the environment profiles, and added a
first-class `testing` profile so CI can label its environment honestly instead of
masquerading as a laptop. The real debt was the dependency set: `requirements.txt`
was a 118-package `pip freeze` in which ~220 MB of the runtime image was packages
NO application module imports. Rebuilt from actual imports to 58 packages — a
measured 377 MB (−66%) off the dependency footprint — and pinned `pytz`, whose
absence had been silently breaking the Market Engine validator since PH2.1. No
application code changed; 934 non-integration tests green before and after.**

> **Design note — a `pip freeze` cannot tell a live dependency from a fossil.**
> That is the whole reason 220 MB of `litellm`, `boto3`, `stripe`, the old
> `google-generativeai` gRPC stack, `pandas` and `numpy` rode into every
> production image: each was added for a feature that was later cut or an SDK that
> was later replaced, and a freeze re-captures all of it forever because it records
> *what is installed*, not *what is used*. The fix is not a bigger prune list — it
> is deriving the set from the code: enumerate the imports, compute the dependency
> closure, and **prove** the result two ways (the closure is closed under its own
> requirements, so nothing is missing; and with every removed module blocked at
> import, the whole runtime graph still loads, so nothing is over-removed). The
> proof is what makes it safe to delete 60 packages without a running container.

## Added

- **`docs/infrastructure/CONFIGURATION.md`** — the configuration reference: the
  single-source-of-truth rule, the source-precedence order, the four environment
  profiles and their severity, the fail-closed validation contract, the dependency
  method + removal ledger, the image-optimization results, migration guidance, and
  the path to cloud (K8s/Vault) portability. Linked from `docs/infrastructure/README.md`.
- **A first-class `testing` environment profile** (`security/secrets.py`):
  `TESTING` joins `KNOWN_ENVIRONMENTS`, and a new `LENIENT_ENVIRONMENTS`
  (`{development, testing}`) names the development-severity set. `APP_ENV=testing`
  is now a recognized, honestly-labelled, non-production environment instead of an
  "unknown APP_ENV" error. Non-production by construction — every production gate
  keys on `env == production`, which `testing` can never satisfy. Three tests added
  (`test_secrets.py`); the core-trio parametrization now covers all four profiles.
- **`requirements.txt` structure** — split into a documented DIRECT section (each
  pin commented with the module that imports it) and a pinned TRANSITIVE section
  (the closure), with a header banning `pip freeze` and explaining how to add a
  dependency correctly.

## Fixed

- **`pytz` was imported but pinned nowhere** — `services/market_engine/validator.py`
  imports it for NSE session math, so the Market Engine validator failed to
  initialize on any clean install (the PH2.1 defect note, now closed). Pinned
  `pytz==2025.2`. Also surfaced (not fixed — business logic, out of scope): in
  `is_market_hours()` the `import pytz` sits *outside* its `try/except ImportError`,
  so the fallback was dead code; pinning `pytz` makes it moot.
- **`docstring_parser` was an unpinned core dependency of `anthropic`** — the freeze
  happened to install it but never recorded it as a first-class pin. Now explicit
  (`docstring_parser==0.18.0`), so the dependency closure is complete.

## Changed

- **`requirements.txt`: 118 → 58 packages.** Removed 62 packages no application
  module imports, in these groups (full ledger in CONFIGURATION.md §7.3): the
  abandoned `litellm`/`openai`/`tiktoken`/`huggingface` AI-abstraction stack; the
  **old** `google-generativeai` SDK and its `grpcio`/`protobuf`/`google-api-*` gRPC
  tail (the app uses the new HTTP `google.genai` SDK); `boto3`/`botocore`/`s3transfer`/`s5cmd`
  (no AWS); `stripe` (no billing code); `pandas`/`numpy` (zero application imports —
  the Dockerfile's "pandas/numpy-heavy" note was stale); `python-jose`/`passlib`
  (the app uses `PyJWT` + `bcrypt`); `python-multipart` (no form/upload route;
  Starlette degrades gracefully); and their transitive tails.
- **`watchfiles` moved to `requirements-dev.txt`** — it powers `uvicorn --reload`,
  which the container entrypoint never uses (dev-only). The runtime image no longer
  carries it.
- **`backend/.env.example`** regenerated from the registry (the `APP_ENV`
  description now lists all four profiles).

## Measured

- **Dependency footprint: 569 MB → ~192 MB `site-packages`; 377 MB (−66%) removed**
  (measured as the summed on-disk size of every removed distribution in the resolved
  venv). Largest wins: `google-api-python-client` 94 MB, `pandas` 71 MB, `litellm`
  47 MB, `numpy` 34 MB, `botocore` 21 MB, `stripe` 18 MB.
- **Projected runtime image: 1.03 GB → ~650 MB** (baseline minus the venv delta;
  the exact end-to-end figure is produced by the CI Docker build, which is
  structurally unchanged — only `requirements.txt` shrank).
- **Verification:** the pruned set is closed under its core requirements (nothing
  missing); with all 62 removed modules blocked at import, the entire runtime module
  graph still imports (nothing over-removed); 934 non-integration tests pass.

## Known limitations

- **The end-to-end image size is projected, not built here** — the Docker daemon
  was unavailable in the sprint environment, so the 377 MB reduction is measured
  directly from the resolved venv (the layer the image copies) rather than from a
  `docker images` readout. The build itself is unchanged; CI produces the exact
  number.
- **`yfinance` remains optional and unpinned** — `services/backtest_engine.py`
  imports it lazily and falls back to synthetic data when absent. Pinning it would
  reintroduce `pandas`+`numpy` (~105 MB) and route market data outside the Market
  Gateway (forbidden by `MARKET_DATA_ARCHITECTURE.md`). Wiring backtesting through
  the gateway is a product decision, out of scope here; until then backtests use
  synthetic data.

---

# Sprint PH2.7 — Production Redis Infrastructure — 2026-07-23

**Redis stops being a service that happens to be running and becomes one with a
lifecycle. A single pooled client for the whole process behind a circuit breaker
that degrades in microseconds and re-tests the dependency instead of giving up on
it; a Pub/Sub subscriber that reconnects with backoff and jitter instead of dying
on the first disconnect; 37 documented server directives in a file both compose
stacks share; and enough diagnostics to tell "Redis is down" apart from "Redis is
up and this replica stopped listening to it". Business logic is untouched — every
`services/cache.py` signature and contract is identical. No Sentinel, no Cluster,
no managed Redis: the seam for all three is one function.**

> **Design note — the two bugs this sprint existed to fix were both invisible.**
> Neither raised an exception. Neither failed a request. Neither failed a health
> check. The cache layer latched `_redis_failed = True` on the *first* failure and
> never cleared it, so one transient blip permanently demoted a process to its
> in-memory fallback — it kept serving, it just stopped sharing state with its
> peers, until someone restarted it for an unrelated reason. And the Pub/Sub
> listener ended permanently on its first exception, while Redis drops subscribers
> *routinely by design* (`client-output-buffer-limit pubsub` exists to disconnect
> a slow consumer). The symptom was WebSocket clients on one replica quietly
> ceasing to receive cross-process events. To the user: "the market went quiet."
> To the operator: nothing at all. **Silent partial degradation is the failure
> mode that survives longest in production**, because every signal anyone is
> watching says the system is fine — which is why this sprint's headline
> deliverable is arguably `/api/diagnostics/redis`, the endpoint that can tell the
> two apart.

## Added

- **`backend/infrastructure/`** — a new package for connections to backing
  services, on the one-module-per-concern shape PH1 established for `security/`
  and PH2.5 for `observability/`. The rule that keeps the boundary honest:
  `services/` may depend on `infrastructure/`; nothing in `infrastructure/` may
  know what a portfolio, a trade or a quote is.
  - **`redis_client.py`** — the one pooled client, with retry, a **circuit
    breaker**, a background `INFO` sampler and a diagnostics snapshot. Pool,
    retry and breaker solve genuinely different problems and are routinely
    confused: the pool amortizes setup and *bounds* concurrency, retry absorbs the
    failure that will succeed if tried again now, the breaker absorbs the one that
    will not. Without a breaker a dead Redis makes the **application** slow —
    every operation pays a full connect timeout and holds an event-loop slot,
    which is the classic cascade where the retry traffic is the outage.
  - **`redis_pubsub.py`** — a supervised subscriber: dedicated connection
    (a `SUBSCRIBE`-mode connection cannot serve a `GET`, so taking one from the
    shared pool removes it from circulation for the process's lifetime),
    reconnect with exponential backoff **and jitter** (without jitter every
    replica retries on the same schedule and arrives at the recovering server in
    synchronised waves), a one-subscriber-per-channel registry (duplicate delivery
    is harder to notice than *no* delivery — the UI just updates twice), and
    graceful shutdown via `get_message(timeout=…)` rather than `listen()`, which
    blocks forever and can only be ended by cancelling mid-await.
- **`docker/redis/redis.conf`** — 37 directives, each with its rationale inline,
  mounted read-only and shared by `docker-compose.yml` **and**
  `docker-compose.secrets.yml`. Only the credential and the two per-environment
  sizes stay on the command line, where later arguments override the file.
- **`GET /api/diagnostics/redis`** — connection (pool occupancy, circuit state,
  last error), **pubsub** (per-channel connected/reconnects/messages/handler
  errors) and server (`INFO` sample with its age). Same operational-token gate as
  `/api/metrics`; the URL is redacted everywhere, because redis-py's connection
  errors stringify to a message containing the password.
- **10 Redis metric families.** `redis_circuit_state` is the one to alert on: it
  is a **leading** indicator, since the fallback keeps serving while it climbs.
  `redis_server_*` gauges come from a background sample on a fixed cadence,
  **never at scrape time** — the same rule PH2.5 applied to `dependency_up`, so
  whoever can reach `/api/metrics` cannot drive dependency load by scraping faster.
- **`docs/infrastructure/REDIS.md`** (+ a `docs/infrastructure/` index) —
  architecture, every server decision, connection lifecycle, the Pub/Sub
  guarantees and the one it cannot make, measured performance, monitoring and
  alert rules, troubleshooting, and the Sentinel → managed → Cluster path.
- **`backend/tests/test_redis_infrastructure.py`** — 50 hermetic tests. Suite
  879 → **929**, all green.
- **Eight configuration variables** registered in `security/secrets.py`:
  `REDIS_MAX_CONNECTIONS`, `REDIS_CONNECT_TIMEOUT_SECONDS`,
  `REDIS_SOCKET_TIMEOUT_SECONDS`, `REDIS_HEALTH_CHECK_INTERVAL_SECONDS`,
  `REDIS_RETRY_ATTEMPTS`, `REDIS_CIRCUIT_FAILURE_THRESHOLD`,
  `REDIS_CIRCUIT_RESET_SECONDS`, `REDIS_STATS_INTERVAL_SECONDS`. All clamp-and-warn.

## Fixed

- **The permanent-failure latch.** `services/cache.py`'s `_redis_failed` flag was
  set on the first exception and never cleared. Replaced by a breaker that opens
  after 5 *consecutive connection-level* failures and half-opens after a cooldown;
  the readiness poll doubles as the half-open trial, so recovery is detected on a
  cadence that already exists. Command errors (`WRONGTYPE`, OOM) deliberately do
  **not** count — a healthy server answering a buggy call site must not be able to
  disable the cache globally.
- **The Pub/Sub listener that never came back.** See the design note above.
- **Health-probe connection churn.** `observability/health.py` built a throwaway
  client on every readiness poll — a TCP connect + AUTH + teardown several times a
  minute, per replica, forever. That was a *correct* workaround for the latch; with
  the latch gone the probe uses the shared client, which also makes it honest: it
  now measures what the application experiences rather than what a fresh private
  connection would have seen.
- **Compose configuration duplication.** The Redis flags were enumerated in both
  `docker-compose.yml` and `docker-compose.secrets.yml`. One `redis.conf` now, so
  a tuning change cannot land in one stack and not the other.
- **Redis teardown on shutdown.** Subscribers are stopped before the pool is
  closed. The reverse order leaves subscribers reconnecting against a client being
  dismantled, so a clean shutdown emits a burst of connection errors and looks, in
  the logs, exactly like a crash.

## Changed

- `services/cache.py` is now pure policy — JSON encoding, TTLs, batching, the
  bounded in-memory fallback. Every signature and contract is unchanged; `_memory`,
  `_MEMORY_MAX_KEYS`, `_get_redis`, `_redis_client` and `_redis_failed` are
  retained (the last two as documented shims) so nothing that imported them breaks.
- A Redis hit is now authoritative **including a miss**: `cache_get` no longer
  falls through to the in-process store when Redis answers "not found", which would
  resurrect a value cached before Redis became the source of truth.
- `cache_delete` always clears the local copy, whatever Redis did — a force-refresh
  that leaves a stale value in one replica's fallback appears to have done nothing,
  on that one replica.
- Values are serialized **before** the Redis call, so an encoding bug is not
  misdiagnosed as a Redis failure and does not count against the breaker.
- Redis container `start_period` 10s → 30s: with AOF persistence a restart replays
  the log and replies `-LOADING` meanwhile, and too short a window turns a normal
  restart into a restart loop.

## Measured

| Path | Cost |
|---|---|
| `cache_get()` facade overhead | 3.5 µs/op |
| `cache_set()` facade overhead (incl. `json.dumps`) | 7.3 µs/op |
| `execute()` while the circuit is **open** | **1.1 µs/op** vs the 1.5 s connect timeout it replaces (~1.3M×) |
| Pub/Sub reconnect after a short blip | attempt 1–3, ~1–4 s after the server accepts connections |

## Known limitations

- **Live-stack verification was not executed** — no Docker daemon on the
  development machine. The four checks that need a real server (restart
  persistence, connection recovery, Pub/Sub reconnect, eviction under pressure) are
  scripted in `docs/infrastructure/REDIS.md` §8 and must be run before production.
- **Single node, no failover.** Explicitly out of scope; the migration path is §9
  and its blast radius is one function, `RedisManager._build_client()`.
- **Pub/Sub remains at-most-once.** Reconnecting restores the stream, not the gap.
  Correct for UI refresh signals; **wrong** for anything where a missed message is
  a lost fact, which needs Redis Streams — not a bigger buffer, which is the
  instinct worth resisting.
- **`allkeys-lru` is only correct while everything in Redis is reconstructible.**
  The day something non-evictable is added, it belongs in MongoDB instead.
- **The breaker is per-process**, so each replica discovers an outage independently.

---

# Sprint PH2.6 — Production Logging Infrastructure — 2026-07-22

**Logs now have an answer to "what stops them?" Five streams split by purpose so
retention can differ per stream, size-triggered rotation to timestamped gzipped
segments, retention bounded by both age and count, every file write behind a
bounded queue so the disk can never stall the event loop, and Docker's own log
capture bounded and made non-blocking. Application logging was not redesigned —
PH2.5's schema, formatters and access log are untouched. File sinks are opt-in
and purely additive: stdout remains unconditional, so `docker logs` and any
attached collector behave exactly as before. No ELK, no Loki, no CloudWatch, no
alerting — this sprint builds the seam those attach to, and PH2.10 attaches
them.**

> **Design note — why stdout was not enough, given twelve-factor says it is.**
> "The platform ships it" assumes a platform. Real deployments spend time in
> states where there is no collector: the first VM, the on-prem install, the
> compliance rule that says "retain authentication events for 90 days on durable
> storage", and the incident where the collector itself is what broke. In every
> one of those, `docker logs` is what you have — and `docker logs` with the
> default `json-file` driver is an **unbounded file on the host**. A service
> that logs steadily fills the disk, and a full disk does not take down the
> noisy container; it takes down every container on the box, plus the SSH daemon
> you were going to use to fix it. So stdout stays the default and the
> recommended path, and this sprint makes the *other* path safe rather than
> pretending it is never taken.

## Added

- **`backend/observability/log_streams.py`** — stream separation and the
  file-sink pipeline. Records are routed into **application / access / security /
  audit / error** entirely by **logger name**, so not one call site changed.
  Streams exist because the five kinds of record have genuinely different
  retention needs: storing "this admin changed that user's role" under the same
  rule as 26 million `GET /api/health` lines forces a choice between paying to
  keep access logs for a year and deleting the audit trail after a week. Both
  are wrong, and no amount of clever querying fixes it once the data is gone.
- **`backend/observability/log_rotation.py`** — the rotation, compression and
  retention policy. Size-triggered rollover to `application.log.20260722T134501.gz`,
  gzip level 6, and pruning by **both** age and count.
- **`docs/operations/LOGGING.md`** — architecture, the five streams, rotation and
  retention, the four-layer redaction policy, Docker integration and the driver
  matrix, measured cost, troubleshooting, and the path to centralized logging.
- **`backend/tests/test_log_infrastructure.py`** — 61 hermetic tests. Suite
  818 → **879**, all green.
- **Eight new configuration variables**, registered in `security/secrets.py` (the
  single source of truth that generates `.env.example` and is drift-checked in
  CI): `LOG_TO_FILES`, `LOG_DIR`, `LOG_FILE_STREAMS`, `LOG_FILE_MAX_BYTES`,
  `LOG_FILE_BACKUP_COUNT`, `LOG_RETENTION_DAYS`, `LOG_FILE_COMPRESS`,
  `LOG_QUEUE_SIZE`. Every one clamps-and-warns rather than raising: a logging
  misconfiguration must never be able to stop a deployment.

## Fixed

- **The request ID was silently `"-"` in every file log record**, while stdout
  showed it correctly for the same record. `StructuredFormatter` reads the
  request ID from a `contextvars` variable *at format time* — and a `ContextVar`
  is bound to the task that set it. The stdout handler formats inline on the
  request's own thread and sees the right value; file handlers format on the
  **queue listener thread**, whose context is empty. The context is now
  snapshotted onto the record at enqueue time, on the calling thread, and the
  formatters prefer that snapshot. A correlation field that is present,
  authoritative-looking and wrong is worse than one that is absent — and it
  would have been wrong on precisely the sink someone greps during an incident,
  because the log platform is the thing that broke.

## Changed

- **`backend/Dockerfile`** — pre-creates `/var/log/stockassist` owned by
  `appuser` (uid 10001), mode 0750. This is the only way to get it right: when
  Docker mounts a named volume onto a path that does **not** exist in the image,
  it creates that path **root-owned**, the non-root process gets `EACCES` on
  first write, and the failure surfaces as a stderr warning followed by silently
  missing log files.
- **`docker-compose.yml`** —
  - The `x-logging` anchor gains `mode: non-blocking` + `max-buffer-size: 4m`.
    The default `blocking` mode means a stalled logging driver blocks `write()`
    to stdout, which in an asyncio server stalls **the event loop** — a slow log
    backend becomes an application-wide outage on requests that never logged
    anything.
  - `backend_logs` named volume, mounted **unconditionally** at
    `/var/log/stockassist` even when file logging is off (where it stays empty
    and costs nothing). Requiring operators to add the mount at the same moment
    they enable file logging guarantees the case where logs land on the
    container's writable layer, look perfectly fine under `docker exec`, and
    vanish on the next deploy.
  - `LOG_DIR` set by Compose, because it must agree with the mount point;
    `LOG_TO_FILES` deliberately **not** set there, since `environment` overrides
    `env_file` and pinning it would silently ignore an operator who enabled file
    logging in `production.env`.
  - The supported logging drivers (Loki, Fluentd/ELK, awslogs, Datadog, Splunk)
    documented at the anchor, with the caveat that every driver except
    `json-file` and `journald` **disables `docker logs`**.

## Design decisions worth recording

- **Timestamped segments, not the stdlib's `.1 .2 .3`.** Shifting every backup
  on each rotation is N renames, makes a file's name change meaning over time
  (today's `.3` is not tomorrow's), and interacts badly with compression. A
  timestamped name is written once, never changes, sorts chronologically under a
  plain `ls`, and makes "the logs from around 13:45" a glob. This is what
  `logrotate`'s `dateext` does.
- **Age is pruned before count.** Count-first can retain a segment that age has
  already expired, quietly breaking a "we do not keep request logs longer than N
  days" commitment — the kind of rule that exists for legal reasons rather than
  disk reasons.
- **The pruner only deletes files whose names it can prove it created.** An
  operator's `application.log.keepme` or an editor swap file is invisible to
  retention.
- **`error.log` is a view, not a partition.** An ERROR from the access logger
  appears in *both* `access.log` and `error.log`; making it exclusive would strip
  the access log of exactly its 5xx lines — the ones you go there to find.
- **Stream ordering is load-bearing.** `security.audit.events` is a child of
  `security`, so audit is matched first; otherwise the security stream swallows
  every audit record and `audit.log` is permanently empty.
- **Compression is synchronous, and that is safe *because* of the queue.**
  Gzipping 50 MB takes ~460 ms; inline that is a p99.99 latency cliff with no
  visible cause. Behind the `QueueListener` it is paid by the log pipeline.
- **The queue is bounded and drops rather than blocks.** An unbounded queue in
  front of a stalled disk is a memory leak with extra steps whose failure mode
  is an OOM kill — losing the logs anyway, plus the service. Drops are counted in
  `log_records_dropped_total`, because dropping telemetry to keep a trading
  backend alive is the right trade and doing it silently is not.

## Measured

| Measurement | Result |
|---|---|
| Caller-thread cost, file sinks **on** | **5.90 µs/record** (169k/sec) |
| Caller-thread cost, stdout only (PH2.5) | 12.34 µs/record (81k/sec) |
| Sustained end-to-end, nothing dropped | ~31,000 records/sec |
| Rotation + gzip | 9.2 ms/MB (~460 ms at the 50 MB default) |
| Compression ratio, realistic JSON logs | 8.1 : 1 |
| Steady-state footprint, all five streams | ~560 MB (2.7 GB worst case) |

Enabling file logging made the caller **faster** (12.34 → 5.90 µs): the queue
moves JSON formatting off the calling thread onto the listener. That is the
entire justification for the queue, and it is nice to see it pay twice.

## Known limitations

1. **Per-container, not centralized** — shipping is PH2.10.
2. **`docker compose down -v` destroys the log volume.** One volume on one host
   is not a retention strategy for an audit trail.
3. **Retention runs at rotation time, not on a timer** — an idle stream keeps
   segments past `LOG_RETENTION_DAYS` until its next rotation. Bounded by the
   count, but not a wall-clock guarantee.
4. **Multi-worker rotation races** if `WEB_CONCURRENCY > 1` (already required to
   be 1 until PH2.8).
5. **`audit.log` is a portable copy, not the record of authority** — MongoDB
   remains that.

---

# Sprint PH2.5 — Production Monitoring & Observability — 2026-07-22

**The backend can now describe its own state. Three distinct health probes, an
in-process metrics registry rendered in Prometheus exposition format, structured
JSON logging on stdout, a request ID carried from the front door through every
log line and audit record, and a diagnostics endpoint that reports which build is
running. No Prometheus server, no Grafana, no alerting, no log shipping — those
are PH2.6 and PH2.10. This sprint makes the application *observable*; the
platform that observes it comes next.**

> **Design note — why there are three health endpoints and not one.** "Is it
> healthy?" is three questions asked by three systems that take three different
> destructive actions. Liveness failure gets the container **killed**; readiness
> failure gets it **removed from the load balancer but left running**; startup
> failure means **keep waiting**. Conflating them is the classic cascading-failure
> mistake: if liveness checks MongoDB, a 60-second database blip makes every
> replica report unhealthy simultaneously, the orchestrator restarts the entire
> fleet at once, and the cold-start reconnect storm lands on a database that was
> already struggling — a recoverable dependency wobble becomes a total,
> self-sustaining outage. So `/api/health/live` performs no I/O at all,
> `/api/health/ready` is the only endpoint that touches Mongo and Redis, and
> `/api/health/startup` exists so an aggressive liveness timer cannot kill a boot
> that legitimately takes 20 Mongo indexes, a broker-session restore, a market
> gateway and four background loops to complete. `backend/docker/healthcheck.sh`
> now explicitly rejects a readiness payload, because pointing a container health
> check at readiness is a real and damaging mistake.

## Added

- **`backend/observability/`** — a new package, one focused module per concern,
  following the shape `backend/security/` established in PH1:
  - `context.py` — request correlation. `contextvars`-backed request ID,
    generated as `uuid4().hex` or adopted from a validated inbound
    `X-Request-ID`. Deliberately dependency-free so `security.audit` can import
    it without a cycle.
  - `logging.py` — structured logging. `StructuredFormatter` (one JSON object per
    line), `HumanFormatter` (dev), `configure_logging()`, message scrubbing,
    third-party noise suppression, and the single-line access log.
  - `metrics.py` — dependency-free `Counter`/`Gauge`/`Histogram` registry with
    Prometheus text exposition, cardinality ceiling, and the four golden signals.
  - `health.py` — probe registry, the `starting → ready → stopping` lifecycle
    state machine, parallel timed probes with a short result cache, and built-in
    MongoDB/Redis probes.
  - `runtime.py` — version, git revision, build date, environment, uptime,
    process facts. Reads no secret values.
  - `middleware.py` — `ObservabilityMiddleware`, one pure-ASGI seam.
  - `routes.py` — the six operational endpoints and the production access gate.
- **Health endpoints**: `/api/health/live` (200 always, zero I/O),
  `/api/health/ready` (Mongo critical + Redis non-critical; 503 while starting or
  draining), `/api/health/startup`, `/api/health` (human aggregate).
- **`/api/metrics`** — Prometheus exposition (`?format=json` for humans).
  `http_requests_total`, `http_request_duration_seconds`,
  `http_request_errors_total` (client/server/exception),
  `http_requests_in_flight`, `app_uptime_seconds`, `app_info`, `dependency_up`
  (1/0/**-1 = not configured**), `health_check_duration_seconds`, process
  memory/FDs, and `metrics_series_dropped_total`.
- **`/api/diagnostics`** — version, git revision, build date, environment,
  start time, uptime, process facts, dependency *presence*.
- **Request correlation end to end** — `X-Request-ID` on every response
  (including errors), on every log line, and on every `security_audit_logs`
  record.
- **`docs/operations/MONITORING.md`** — endpoints, metrics, logging, correlation,
  configuration, a troubleshooting guide, measured overhead, and the PH2.6/PH2.10
  handoff including draft alert rules.
- **`backend/tests/test_observability.py`** — 123 hermetic tests.

## Changed

- **`backend/server.py`** — `configure_logging()` moved to the **top** of the
  file, replacing the `logging.basicConfig` line that sat at line 5392. It ran
  *after* every import, so the ~40 modules that log at import time — the boot
  sequence, the part you read when a container will not start — were the least
  legible output in the log. Also: observability router registered, middleware
  applied **last** (so it is outermost), health checks registered at import time,
  `mark_started()` as the final statement of startup, `mark_stopping()` as the
  **first** statement of shutdown.
- **`security/audit.py`** — `redact_fields()` made public so observability reuses
  the same sensitive-key list rather than maintaining a second one; `request_context`
  now reads the request ID from the context first. *PH1.10 shipped a `request_id`
  field, but nothing ever generated one — it was `None` on every record in
  practice. It is now populated.*
- **`security/cors.py`** — `X-Request-ID` added to `EXPOSE_HEADERS` (previously
  empty). Without it the browser receives the header but JavaScript cannot read
  it, so the SPA could never show a user their correlation ID.
- **`security/rate_limit.py`** — the six operational paths added to
  `_MIDDLEWARE_EXEMPT_PATHS`. A probe cadence across a kubelet, a load balancer
  and an uptime monitor would otherwise exhaust the anonymous per-IP budget and
  return 429, which the orchestrator reads as "unhealthy" — the rate limiter
  manufacturing the outage it exists to prevent.
- **`security/secrets.py`** — 12 observability variables registered in
  `SECRET_REGISTRY` (`LOG_*`, `METRICS_*`, `HEALTH_*`, `APP_VERSION`, `VCS_REF`,
  `BUILD_DATE`); `backend/.env.example` regenerated (52 variables).
- **`backend/Dockerfile`** — `APP_VERSION`/`VCS_REF`/`BUILD_DATE` promoted from
  build args to runtime `ENV`. They were already OCI labels, but a process cannot
  read its own image metadata; promoting them is what lets the application report
  its own build.
- **`backend/docker/healthcheck.sh`** — accepts both the `/api` (`"running"`) and
  `/api/health/live` (`"ok"`) contracts; explicitly rejects a readiness payload.
- **`backend/tests/test_cors_hardening.py`** — the "no response headers exposed"
  assertion updated to the exact one-header list, with the justification recorded.

## Fixed

- Two defects found by exercising the real boot path, both in code added this
  sprint: the message scrubber accepted bare whitespace as a key/value separator
  and so corrupted ordinary prose (the config validator's own warning came out as
  `…username:password [REDACTED] database is…`); and histogram bucket bounds were
  rendered with fixed six-decimal padding (`le="0.005000"` beside `le="1"`),
  which would also have truncated a sub-microsecond `_sum`. Both now have
  regression tests.

## Known limitations

1. Metrics are **per-process**; with `WEB_CONCURRENCY > 1` a scraper reaches one
   worker at random. Aggregation is a PH2.10 problem.
2. No WebSocket instrumentation — a 40-minute connection in the same histogram as
   a 12ms REST call makes every percentile meaningless.
3. No distributed tracing; request IDs correlate within this service only.
4. No alerting and no log shipping — PH2.10 and PH2.6 respectively.
5. `/api/admin/system/health` and `/api/admin/apis/health` still return partly
   fabricated data (hard-coded latencies). Admin-dashboard endpoints, out of
   scope here; they should be re-pointed at this module's real data later.

## Verification

818 hermetic tests pass (695 before this sprint, +123). Full `flake8` clean on
every added file; the repo-wide correctness subset remains at zero findings.
Boot verified end to end against a real MongoDB with `LOG_FORMAT=json`.
Measured: middleware overhead < 0.1 ms/request, `/api/health/live` ~0.76 ms,
`/api/metrics` render ~1.2 ms, module import ~0.7 s.

---

# Sprint PH2.4 — Production GitHub Actions CI — 2026-07-22

**Every push and every pull request is now verified by a machine. Five
workflows answer five separate questions — is the source correct, is the
*artifact* correct, is someone else's code safe, did we leak configuration, is
our own code vulnerable — and no check runs in more than one of them. CI only:
nothing in this sprint pushes an image, touches a registry, or contacts a
server.**

> **Design note — why some lint gates are advisory, and why that is not
> sloppiness.** The backend predates its linters: `black` would reformat 116 of
> 119 files, `isort` disagrees with 70, full `flake8` reports 462 findings.
> There were three options. Land a 116-file mechanical reformat in the same PR
> as the pipeline — unreviewable, and it destroys `git blame` across every
> security module PH1 just hardened. Turn the gates on anyway and accept a
> permanently red `main` — worse than no build, because a red build everyone
> ignores trains the team that red means nothing, and the *next* failure, a real
> one, gets ignored too. Or: gate what can genuinely be held at zero, measure
> the rest in the open, and hold new files to the full standard so the backlog
> can only shrink. The third is what shipped. The **correctness** subset
> (`E9,F63,F7,F82,F811,F632` — syntax errors, undefined names, redefinitions,
> `is` against a literal) is blocking repo-wide and sits at **zero findings**;
> files *added* by a pull request are blocking under the full standard;
> everything else reports into the job summary with a documented exit path.

Added

- **`.github/workflows/backend-ci.yml`** — three parallel jobs behind one
  aggregate gate. `quality` (lint/format/static analysis, per the adoption
  model above). `build` — three widening circles: `compileall` over every
  shipped source, `import server` **with runtime dependencies only** (so a
  module that imports a dev-only package fails here rather than in the
  production image), and the startup validator exercised across all three
  environments *including a negative case*. That negative case is the half
  teams omit: a validator accidentally reduced to `return True` passes every
  positive test perfectly. `test` — `pytest -m "not integration"`, 695 tests,
  with JUnit XML uploaded `if: always()`.
- **`.github/workflows/docker-build.yml`** — builds the real production image
  and then does the one thing a build never does: **starts it**. hadolint,
  buildx with a GHA layer cache, static assertions on the artifact (non-root
  uid 10001, no `pip`, no compiler, `/app` unwritable by the app user,
  `HEALTHCHECK` present, revision label carrying the building SHA), then three
  smoke tests — **A** the container refuses to start with no configuration and
  names every missing secret; **B** a full synthetic production configuration
  validates via the entrypoint's `exec "$@"` escape hatch, and no secret *value*
  appears in the log; **C** it boots against real MongoDB and Redis, serves
  `/api` with the expected payload, passes its own health-check script, and
  exits 0 on SIGTERM. Nothing is pushed and the token cannot write packages.
- **`.github/workflows/dependency-audit.yml`** — `pip-audit --strict` and
  `npm audit`, moved out of `security-audit.yml`, plus a **suppression-expiry
  ratchet**. The 15 currently-suppressed advisories (see below) now carry a
  review date: from `2026-08-22` every run warns, and 30 days later the build
  fails until someone re-argues the case. Without a mechanism like this,
  `--ignore-vuln` is where vulnerabilities go to be forgotten.
- **`.github/workflows/codeql.yml`** — taint-tracking SAST for Python and
  JavaScript/TypeScript. An `eligibility` job reads
  `github.event.repository.private` at run time and **skips cleanly** on a
  private repository without Advanced Security, rather than failing every run
  with a licensing error — a permanently red check trains people to ignore red
  checks. It activates automatically if the repository is made public.
- **`.github/actions/setup-backend/`** — composite action providing the seven
  steps four jobs share. A composite action rather than a reusable workflow
  because this is shared *steps*, not a shared *job*: a `workflow_call` would
  add a runner and a checkout to four jobs for nothing. It caches the **built
  virtualenv** keyed on the requirements hash, not pip's download cache — the
  latter still pays for resolution and unpacking, roughly 60-70% of the wall
  clock. There is deliberately **no `restore-keys` fallback**: a partial-match
  restore hands a job an environment that does not match its lockfile, and a
  miss costs two minutes where a wrong hit costs an afternoon.
- **`backend/pyproject.toml` + `backend/.flake8`** — pytest, black, isort and
  mypy configuration. The point is not tidiness: CI cannot be built on an
  invocation that lives in someone's shell history, and a pipeline that selects
  tests differently from the developer who wrote them will disagree with that
  developer at the worst possible moment. `--strict-markers` turns a typo'd
  marker into a collection error rather than a silently-ignored decorator.
- **`.hadolint.yaml`** — Dockerfile lint policy. Two rules ignored, each with a
  written reason; an unexplained ignore is a defect.
- **`docs/deployment/GITHUB_ACTIONS.md`** — workflow architecture, triggers,
  job order, the lint adoption model and its exit path, cache strategy, the
  accepted-risk register, verification results, troubleshooting table, known
  limitations L1-L10, and the CD boundary.

Changed

- **`backend/tests/conftest.py`** — a `pytest_collection_modifyitems` hook marks
  the six live-server suites (`test_backend.py`, `test_phase2.py`,
  `test_phase4-7.py`) as `integration`, so CI selects with
  `-m "not integration"`. The list lives **in the test tree, not in YAML**: put
  it in a workflow file and the next person to add a live-server suite never
  finds it, their suite runs in CI, reaches no server, and fails for a reason
  that looks nothing like the cause.
- **`.github/workflows/security-audit.yml`** — re-scoped to secret and
  configuration hygiene. `pip-audit`/`npm audit` moved to `dependency-audit.yml`;
  the standalone `pip check` job was deleted because the composite action runs
  `pip check` in every job that installs dependencies. No check now runs twice
  anywhere in the pipeline. `config-sync` uses the composite action, which also
  fixes its missing-dependency install.
- **`.claude/TESTING.md`** — the CI/CD section now separates the target pipeline
  from what is actually implemented, with owners for each gap.
- **`.gitignore`** — `.venv-ci/`, `.mypy_cache/`, `test-results/`.

Fixed

- **`backend/tests/test_trading_engine.py::test_run_cycle_trails_and_books_targets`**
  — a stale exact-equality assertion. `run_cycle` gained a `closed_trades` key
  in its return contract and the test was never updated, so the hermetic suite
  had a standing failure (694 passed / 1 failed, carried since PH2.3). Landing
  CI over a known-failing suite would have meant a red `main` on day one. The
  product code was correct; the assertion was not. Suite is now **695 passed**.

Known limitations

- **L1 — `docker-build.yml` has never been executed.** The Docker daemon was
  unavailable on the development machine during this sprint. The workflow is
  verified by YAML parse, `bash -n` over every `run:` block, and review against
  the PH2.1 Dockerfile contract, but its build and three smoke tests will run
  for the first time on the first push. Treat a fix-up commit as expected.
- **L2 — 15 dependency advisories are suppressed**, and this is the sprint's
  most significant *finding* rather than its work: `starlette 0.37.2` (7
  advisories, fixes available, held by the `fastapi==0.110.1` pin — highest
  priority, it is the ASGI layer every request traverses), `litellm 1.80.0` (7
  advisories; PH2.1 established it is **not imported by any application code**,
  so the fix is removal, not an upgrade), `ecdsa 0.19.2` (1, no fix released,
  not reachable — JWTs are HS256). Previously suppressed as a bare flag list;
  now each carries a written reachability argument and a dated review.
- **L3** — 98 integration tests excluded (PH3.1 / PH2.6 own the conversion).
- **L4** — no frontend build/lint/test job (PH3.3).
- **L5** — no coverage measurement; needs `pytest-cov` pinned into
  `requirements-dev.txt`.
- **L6** — **branch protection is not configured.** Every gate in this sprint is
  advisory until `main` requires it; a red pipeline can still be merged today.
  PH2.5 owns it, and the aggregate job names (`backend-ci`, `docker-build`,
  `dependency-audit`, `codeql`) exist so that configuration never needs to
  change again.
- **L7-L10** — no PR template, actions pinned by tag rather than commit SHA, no
  image vulnerability scan. Full table in `docs/deployment/GITHUB_ACTIONS.md`
  §13.

---

# Sprint PH2.3 — Production Secrets Management — 2026-07-22

**Credentials no longer have to be plaintext environment variables. `security/
secrets.py` gained a source-resolution layer — Docker/Swarm/Kubernetes secrets,
the `<NAME>_FILE` convention, and plaintext env as the development fallback —
applied through one precedence order to every variable, materialized into
`os.environ` once at boot. No application logic changed, no call site changed,
and no existing deployment breaks: the migration is opt-in and per-secret.**

> **Design note — why the loader hydrates `os.environ` instead of introducing an
> accessor.** About thirty modules already read configuration through call-time
> `os.environ.get(...)` resolvers. Routing them all through a new accessor would
> have meant a thirty-file refactor of security-critical code, and would have
> created a rule that a future module can forget — the first one that forgets it
> silently loses file support. Resolving centrally and writing the result into
> the environment *before any of them is imported* gives every consumer file
> support for free, keeps exactly one place that knows how to find a secret, and
> — because those resolvers read at call time rather than capturing at import —
> is what makes in-place rotation propagate to live code. The honest cost: after
> hydration a secret is in the process environment, so `/proc/<pid>/environ`
> inside the container still shows it. What goes away is the far larger
> host-side surface — `docker inspect`, container metadata, child-process
> inheritance. Recorded as limitation L1.

Added

- **Source resolution in `backend/security/secrets.py`** — `resolve_all()` /
  `resolve_secret()` apply one precedence order to every variable: (1) a
  `<NAME>_FILE` pointer, (2) a discovered `$SECRETS_DIR/<name>` mount, (3) a
  plaintext environment variable. `load_secrets()` materializes the result into
  `os.environ`; `validate_config()` calls it first, so validation and the running
  application can never see different values. Two properties are load-bearing:
  **an unreadable pointer never falls back to the plaintext variable** (the
  silent downgrade is exactly how a rotation appears to succeed while the old key
  stays in use), and **two sources for one secret is a boot error, not a merge**
  (two sources means two owners; guessing lets a rotated value be shadowed by a
  stale one). Discovery checks both `JWT_SECRET` and `jwt_secret` because the
  ecosystem is split — Compose/Swarm names are conventionally lowercase,
  Kubernetes keys usually are not — and is limited to registered names so a stray
  file in the mount cannot invent an environment variable.
- **`reload_secrets()`** — re-reads file sources and reports what changed by
  keyed-HMAC fingerprint, never by value. Works because rotated Docker configs
  and Kubernetes projected volumes rewrite the *same path* in place. Also drops a
  revoked secret from `os.environ` rather than leaving the loader's own stale
  write behind: a deleted secret must stop working. Nothing calls it
  automatically yet (L5).
- **`docker-compose.secrets.yml`** — an opt-in overlay delivering `mongo_url`,
  `redis_url`, `jwt_secret` and the MongoDB root credentials as file-mounted
  Docker secrets. Applied over `-f docker-compose.yml` (the explicit production
  path, not the dev default). Retracts the base file's plaintext values with an
  explicit empty string rather than null: `KEY: ~` means "inherit from the
  invoking shell", so an operator with `MONGO_URL` exported in their terminal
  would have it injected; `KEY: ""` is unconditional and identical on every
  machine. Verified against a hostile host environment.
- **`secrets/generate.sh`** + `secrets/README.md` + a deny-by-default
  `secrets/.gitignore` — generates 48-byte CSPRNG values at `chmod 600` (umask
  set *before* the write, never tightened after), composes `mongo_url`/`redis_url`
  from the passwords it just wrote so a URI and its credential cannot drift, and
  emits a real Fernet key for `BROKER_TOKEN_KEY`. It deliberately **refuses to
  invent third-party credentials** — a placeholder Anthropic key is
  indistinguishable from a real one to everything downstream. `--check` and
  `--rotate` modes.
- **`docs/deployment/SECRETS.md`** — the mechanism: architecture diagram,
  precedence table, the full validation matrix, workflows for
  development/Compose/Docker-Secrets, per-secret rotation blast radius, the
  Swarm/Kubernetes/Vault migration paths (documented, not implemented), seven
  known limitations, and a troubleshooting table.

Changed

- **Validation now covers credential *shape*, not only presence and length**
  (the sprint brief's Mongo/Redis/OAuth/API-key/encryption-key requirements).
  New in production: `MONGO_URL` must carry `user:password` (a credential-free
  URI means the database is unauthenticated or every query fails auth — the 2017
  ransom-wave configuration), `REDIS_URL` must carry a password (Redis has no
  user model, and `CONFIG SET dir` + `SAVE` makes an open instance an RCE
  primitive, not merely a cache leak), and provider key shapes are warned on.
  New in **every** environment: `BROKER_TOKEN_KEY` must be a valid Fernet key —
  an invalid one does not fail at boot, it fails the first time a user connects a
  broker, weeks later.
- **Low-entropy secret detection** (`looks_weak`) — the length check is gameable:
  `aaaaaaaa…` clears "≥ 32 characters" and does not survive ten seconds of
  offline attack. Rules are deliberately conservative (< 8 chars, ≤ 4 distinct
  characters, < 8 distinct at ≥ 16 chars, keyboard/digit runs) because a false
  positive blocks a production boot; a real `token_urlsafe(48)` clears all of
  them by a wide margin. Error in production, warning in development.
- **Delivery-posture reporting** — a *sensitive* secret arriving as plaintext is
  a warning at every production boot, listing exactly which ones. Development is
  deliberately not nagged: a warning that fires on every laptop boot is one
  nobody reads in production either. `REQUIRE_FILE_SECRETS=true` promotes it to a
  boot error, so a completed migration cannot silently regress.
- `ConfigReport` gained `sources` / `from_file()` / `from_env()`; the startup
  summary now reports `file-backed=N plaintext=N`. `docker/entrypoint.sh` prints
  the file-backed names — names only, never values.
- `server.py` — the boot comment now documents that hydration must precede every
  other import, and why. No behavioural change beyond what `validate_config()`
  now does.
- `production.env.example`, `compose.env.example`, `.claude/SECRETS.md`,
  `docs/deployment/README.md`, `docs/deployment/DOCKER_COMPOSE.md` (L2/L3 and §11
  corrected), `backend/security/__init__.py`, `backend/.env.example`
  (regenerated).

Fixed (found during verification, in this sprint's own code)

- **A `_FILE` pointer aimed inside `$SECRETS_DIR` conflicted with itself.** The
  first end-to-end run against a real mount failed: `JWT_SECRET_FILE=/run/secrets/
  jwt_secret` names precisely the path discovery scans, so the pointer and the
  discovery were counted as two rival sources and the boot was refused — meaning
  the configuration this sprint documents would have failed on every deploy.
  Comparing paths would still have been wrong on a case-insensitive filesystem
  (macOS, Windows), where `JWT_SECRET` and `jwt_secret` are one file under two
  names. Fixed by having a successful pointer *suppress* discovery: an explicit
  instruction leaves nothing to disambiguate. The conflict rule keeps its teeth
  where ambiguity is real — a file source competing with a plaintext env var.

Testing

- `backend/tests/test_secret_loading.py` — **68 new hermetic tests** covering
  every item on the sprint's testing checklist: env loading, Docker secret
  discovery (lowercase and exact), the `_FILE` convention, precedence, conflicts,
  missing/empty/oversized/binary/directory sources, placeholder and weak-value
  rejection, production boot failure, development fallback, rotation, and four
  tests asserting that no error message, summary line or `repr` ever contains a
  secret value. File reads go through an injected reader; the cases that are
  specifically *about* the filesystem use `tmp_path` and the real reader.
- `backend/tests/test_secrets.py` — `base_prod_env` now uses a credentialed
  `MONGO_URL`, since a credential-free one is a production error under the new
  rule. 38 tests, unchanged otherwise.
- Full hermetic backend suite: **694 passed, 1 failed** — the failure is the
  documented pre-existing `test_trading_engine::test_run_cycle_trails_and_books_targets`
  (unrelated, owned by PH3.1). Baseline was 626 passed with the same single
  failure.
- End-to-end verification against a real Docker-style mount: secrets resolve,
  `security.jwt` (an unmodified consumer) reads the file-backed value, no secret
  appears in the `docker inspect` view, in-place rotation propagates to live code,
  and `REQUIRE_FILE_SECRETS=true` refuses the boot with a value-free message.
- `docker compose -f docker-compose.yml -f docker-compose.secrets.yml config`
  validates; retraction verified against a shell exporting hostile values.

Known limitations

- **L1** — after hydration, secrets are in the process environment (by design;
  see the design note above).
- **L2** — MongoDB's *app-user* credentials remain plaintext env vars.
  `docker/mongodb/init-app-user.js` runs under `mongosh` and can only reach
  `process.env`. Bounded exposure: consumed only on first initialization of an
  empty volume, and the account it creates holds `readWrite` on one database. The
  **root** password is fully file-backed.
- **L3** — Redis's password is only partially covered. The official image has no
  `_FILE` support; the overlay's `sh -c` wrapper removes it from `docker
  inspect`'s `Cmd` but leaves it in `redis-server`'s argv inside that container,
  and `REDISCLI_AUTH` must stay an env var because the healthcheck runs as a bare
  exec. Closing both needs a generated `redis.conf`.
- **L4** — rotating `BROKER_TOKEN_KEY` is destructive: no re-encryption migration
  exists, so stored broker tokens become undecryptable.
- **L5** — no automatic reload trigger. `reload_secrets()` exists and is tested,
  but nothing calls it; rotation in practice still means restart.
- **L6** — under plain Compose the secret files are plaintext on the host disk.
  Inherent to file-based Compose secrets; Swarm removes it with no file change.
- **L7** — no CI assertion that nothing under `secrets/` was force-added. Belongs
  in PH2.5.

Not in scope (unchanged, per the sprint brief): Vault, AWS/Azure/GCP secret
managers, Kubernetes manifests, CI/CD, authentication, business logic, trading,
AI. PH2.2's limitation **L2** (in-container hot reload) also remains open — this
sprint sharpened rather than resolved it, since a bind-mounted `.env` would now
be a competing source the loader refuses to boot on.

---

# Sprint PH2.2 — Production Docker Compose — 2026-07-22

**The backend stack now starts with one command. PH2.1 produced a deployable
image; PH2.2 turns it into a running system — backend, MongoDB and Redis, with
network segmentation, named volumes, health-gated startup ordering and no
credential of any kind hardcoded. Orchestration only: no CI, no CD, no
Kubernetes, no reverse proxy, no TLS, and no application code changed.**

> **Sprint numbering note.** `PRODUCTION_ROADMAP.md` v1.2 assigns PH2.2 to the
> *frontend* production Dockerfile and PH2.3 to the compose split. The sprint as
> commissioned re-sequenced these: compose orchestration was pulled forward to
> PH2.2 and secrets management became PH2.3. The roadmap document has not been
> renumbered — the dependency graph is unchanged and the frontend image sprint
> is simply still outstanding. See "Known limitations" L6.

Added

- `docker-compose.yml` (replaces the previous file) — the **production-shaped
  base stack**. Three services and nothing else: `backend` (the PH2.1 image,
  built from `./backend` with the OCI provenance build args — build logic is
  *not* duplicated, the Dockerfile stays the single authority), `mongo` and
  `redis`. Explicit project `name: stockassist` so volume and container names do
  not shift with the checkout directory. Cross-cutting policy is defined once
  through YAML anchors: `restart: unless-stopped`, `no-new-privileges:true`, and
  bounded json-file logging (10 MB × 3 — the default driver is unbounded, and a
  full disk takes down every container on the host, not just the noisy one).
  Deliberately contains **no `healthcheck:` block for the backend**: the image
  already declares one, and restating it here would fork the definition so that
  `docker run` and `docker compose up` probe the same image differently.
  `stop_grace_period: 30s` because the entrypoint gives uvicorn 20s to drain and
  Docker's default grace period is 10s — without it every `down` would `SIGKILL`
  the server mid-drain. Backend port publishes to **127.0.0.1 by default**:
  Docker writes published ports into the `DOCKER-USER` iptables chain, which is
  evaluated *before* ufw/firewalld, so a `0.0.0.0` bind on a cloud VM is
  internet-reachable regardless of the host firewall.
- `docker-compose.override.yml` — the **development overlay**, auto-merged by
  Compose so `docker compose up` is the developer path and
  `-f docker-compose.yml` is the explicit production path (base = strict,
  overlay = relaxed; forgetting a flag can never *add* a database browser to a
  production stack). Adds Mongo Express, Redis Insight (`--profile tools`), n8n
  (`--profile automation`), loopback-published database ports and
  `APP_ENV=development`. Deliberately **no source bind mount and no
  `--reload`** — mounting `./backend` over `/app` would reintroduce
  `backend/.env` inside the container, and `server.py` calls
  `load_dotenv(override=True)`, so that file would silently override every
  variable Compose injects.
- `docker/mongodb/init-app-user.js` — first-boot provisioning of a
  **least-privilege application account** (`readWrite` on the application
  database only). The mongo image creates a *cluster root* user; handing those
  credentials to the application would mean an injection or a leaked container
  environment carries the ability to drop every database and create users. The
  root password never enters the backend container. Verified: the app user is
  denied `admin` reads, `createUser` and `dropDatabase`, and `listDatabases`
  returns only its own database.
- `compose.env.example` — template for the project-root `.env` that **Compose
  itself** reads. This is the second half of a deliberate two-file split:
  infrastructure credentials (Mongo/Redis passwords, host ports, image tags) are
  handed to the specific service that needs them, while application secrets
  reach the backend through `production.env`. A least-privilege boundary and a
  rotation boundary, not bookkeeping.
- `docs/deployment/DOCKER_COMPOSE.md` — full stack documentation: architecture
  and network diagrams, per-service rationale, network/volume design, the
  environment split, operator guide, measured startup timings, a 16-point
  verification table, seven known limitations, a troubleshooting matrix, and the
  handoff to PH2.3.

Changed

- `production.env.example` — documents the PH2.2 two-file model and records that
  `MONGO_URL` and `REDIS_URL` are **overridden by Compose** (a service's
  `environment:` outranks its `env_file`), so a stale value cannot break the
  stack's own wiring.
- `docs/deployment/README.md` — indexes the new document and both env templates.

Security findings closed or recorded

- **MongoDB now requires authentication.** The previous compose file ran mongo
  with no credentials and published 27017 on `0.0.0.0` — the exact configuration
  behind the 2017–2020 MongoDB ransom wave. The production stack now enables
  `--auth`, publishes no host port at all, and sits on an `internal: true`
  network with no route to or from the internet.
- **Redis is password-protected.** An unauthenticated Redis is a remote code
  execution primitive, not merely a data leak (`CONFIG SET dir` + `SAVE` writes
  arbitrary files). Verified: unauthenticated commands are refused with `NOAUTH`.
- **n8n basic auth was already dead.** The previous compose file set
  `N8N_BASIC_AUTH_ACTIVE` / `_USER` / `_PASSWORD`; those variables were removed
  upstream in n8n 1.0 and are silently ignored by 1.70. Verified during this
  sprint: with all three set, `GET http://127.0.0.1:5678/` answered **200 with
  no credentials**. They are not carried forward — a control that does nothing
  is worse than a documented absence. Access now rests on the loopback-only
  binding plus n8n's own owner-account setup, both documented.
- **`internal: true` silently disables port publishing.** A container attached
  only to an internal network accepts a `ports:` entry, records the binding, and
  never listens. Found while verifying the overlay; fixed by attaching mongo and
  redis to `edge` in the development overlay only.

Verified

- 16 checks, all passing: health-gated startup ordering; least-privilege
  database access (5 probes); network egress isolation (data tier has none, edge
  tier does); no host ports for the databases in the production stack; named
  volumes surviving a full `down` + `up` (14 collections, probe document, Redis
  key); restart policy and AOF recovery after a Redis crash; and three
  fail-closed paths — missing `production.env`, missing `REDIS_PASSWORD`, and a
  weak `JWT_SECRET` under `APP_ENV=production` (backend exits 1, restart-loops,
  never reports healthy, never serves traffic).
- Timings (Apple silicon, image cached): cold start to all-healthy **13–32 s**,
  warm start **12–14 s**, development stack with tools **16 s**, graceful
  `down` **2 s**.

Known limitations

- **L1** — the Redis pub/sub listener stops after ~3 s
  (`backend/services/cache.py:47` shares one client with `socket_timeout=3`,
  and `pubsub.listen()` blocks longer by design). A pre-existing application
  defect that was unreachable until this sprint put a Redis in the stack. No
  functional regression at `WEB_CONCURRENCY=1` with one replica — the in-process
  event bus carries every event and the cache path works normally. The fix
  belongs to **PH2.8**, which owns cross-process fan-out and can test it.
- **L2** — no in-container hot reload (PH2.3). **L3** — secrets are plaintext on
  disk and visible in `docker inspect` (PH2.3). **L4** — single-node MongoDB, no
  replica set, so no transactions or change streams. **L5** — no automated
  backups (PH2.10). **L6** — the frontend is not in the stack: it has no
  Dockerfile, and the service the previous compose file declared referenced
  `frontend/Dockerfile`, which has never existed in this repository, so it could
  never have built. **L7** — `read_only: true` is not enabled for the backend
  (data-science dependencies write to a `$HOME` cache on first use).

---

# Sprint PH2.1 — Backend Production Dockerfile — 2026-07-22

**First PH2 sprint. The backend is now a reproducible, non-root, fail-closed
container image. This is the deployment primitive every later PH2 sprint builds
on: Compose (PH2.3), CI image builds (PH2.6), and CD (PH2.7) all consume it.
Backend containerization only — no Compose, no CI, no Redis, no Kubernetes.**

Added

- `backend/Dockerfile` — two-stage production build.
  **Stage 1 (builder):** `python:3.11-slim-bookworm` + `build-essential`,
  installs `requirements.txt` (runtime deps only — `requirements-dev.txt` is
  never installed, per PH1.11/M14) into a relocatable venv at `/opt/venv`, then
  prunes bundled library test suites and `pip`.
  **Stage 2 (runtime):** the same slim base with **no compiler, no headers, no
  package installer**; copies `/opt/venv` and the application source; creates a
  dedicated `appuser` (fixed uid/gid **10001**, `nologin`, no password); source
  and venv stay **root-owned** so the running process can read and execute its
  code but **cannot modify it**; OCI provenance labels (`APP_VERSION`,
  `VCS_REF`, `BUILD_DATE` build args); `EXPOSE 8000`; `HEALTHCHECK`;
  exec-form `ENTRYPOINT` with empty `CMD`.
  `slim` over `alpine` is deliberate: musl invalidates the manylinux wheels for
  pandas/numpy/cryptography/grpcio, turning a ~1-minute dependency install into
  15–30 minutes of source compilation for a slower runtime.
- `backend/.dockerignore` — the single auditable boundary between repo and
  image. Excludes **every** dotenv file, `venv/`, `tests/`, `.git/`, caches and
  build output. The dotenv exclusion is a correctness control as much as a
  secret-leak control: `server.py` calls `load_dotenv(..., override=True)`, so a
  `.env` inside the image would silently **override the environment variables
  injected by the orchestrator at runtime**. Reduces the shipped build context
  from **620 MB → 2.5 MB**.
- `backend/docker/entrypoint.sh` — PID 1. POSIX `sh` (the runtime image has no
  bash). Reads all configuration from the environment with production-safe
  defaults; performs structural validation (`APP_ENV`, `PORT`,
  `WEB_CONCURRENCY`); then delegates full validation to the application's own
  authority, `security/secrets.py` `validate_config()` (PH1.8), printing its
  aggregated, value-free report and **exiting 1** rather than starting
  misconfigured — no second source of truth in shell. Runs any executable in
  `docker/pre-start.d/` (the `postgres`/`nginx` convention) as a working
  extension point for future migrations, then **`exec`s** uvicorn so the server
  becomes PID 1 and receives `SIGTERM` directly. Explicit-args mode
  (`docker run <image> <cmd>`) runs one-shot jobs against the same validated
  configuration. Launches with `--no-server-header`, `--proxy-headers`,
  `--forwarded-allow-ips`, `--timeout-graceful-shutdown`; **never** `--reload`.
- `backend/docker/healthcheck.sh` — liveness probe honouring Docker's exit-code
  contract (0 healthy / 1 unhealthy, nothing else). Written against the Python
  **standard library** specifically so `curl`/`wget` never enter the runtime
  image. Probes `127.0.0.1` (measures the application, not the network path to
  it) at `/api` — unauthenticated, already rate-limit-exempt in
  `security/rate_limit.py`, and **dependency-free by design**: a Mongo round-trip
  in a *liveness* probe would mark every replica unhealthy during a database
  blip and restart the whole fleet. Validates the response body, not just the
  status code.
- `production.env.example` (repo root) — operator-facing runtime environment
  template; the deployment-time counterpart to the developer-facing,
  registry-generated `backend/.env.example`. Placeholders only. Documents the
  container-runtime variables, the required core set, and — explicitly, commented
  out — the variables that must **not** appear in production.
- `docs/deployment/DOCKER.md` — full container architecture document: rationale,
  diagrams, multi-stage strategy, layer-caching and size analysis with measured
  numbers, security decisions, runtime configuration, health-check design,
  operator guide, known limitations, PH2.2–PH2.10 forward path, and a
  troubleshooting matrix.
- `docs/deployment/README.md`, and a `deployment/` entry in `docs/README.md`.

Changed

- `.claude/PRODUCTION_ROADMAP.md`, `.claude/TASK.md` — PH2.1 marked COMPLETE
  with delivered/met/missed detail.

Verification

- Cold build **2m 44s** (first build, incl. base-image pull) / **3m 21s**
  (`--no-cache`); **4.5s** rebuild after a source-only change — the dependency
  layer cache holds, which is the whole point of `COPY requirements.txt` before
  `COPY . .`. Final image **1.03 GB**, `/app` 2.8 MB.
- Boots healthy against live MongoDB in **2.5s**; Docker reports `healthy`.
- Fail-closed proven: missing secrets, placeholder-looking secrets, invalid
  `APP_ENV` and non-numeric `PORT` each abort with a clean operator-facing
  message and **exit 1**.
- Non-root confirmed (`uid=10001`); the process **cannot** write `/app` or
  `/opt/venv`; `pip`, `curl`, `wget` and `gcc` all absent; no `.env`, `tests/`
  or `venv/` in the image (`/app` totals 2.8 MB).
- Graceful shutdown: `docker stop` → **exit 0 in 1.2s**, FastAPI `shutdown` event
  ran (scheduler stopped, Mongo client closed) — signals reach PID 1.
- Runs healthy under `--read-only --tmpfs /tmp --cap-drop=ALL
  --security-opt no-new-privileges:true`.
- PH1.4 security headers verified intact through the container; no `server:`
  header leaked.

Known limitations

- **Image is 1.03 GB against the roadmap's < 400 MB target.** Every image-level
  lever was applied and measured (multi-stage −300 MB, bundled test suites
  −66 MB, `pip` −16 MB; `strip` measured at **0 MB** because manylinux wheels
  ship pre-stripped, and `--no-compile` was rejected because it trades 158 MB of
  image for a permanent per-start compile cost). The residual is the dependency
  set, not the Dockerfile: `googleapiclient` (97 MB), `litellm` (55 MB),
  `boto3`/`botocore`/`s3transfer` (~32 MB), `stripe` (24 MB) and `s5cmd` (15 MB)
  are **imported by zero application code** — ≈220 MB, plus the CVE surface.
  `s5cmd` in particular is a standalone S3 CLI, an ideal exfiltration tool.
  **A dependency-pruning sprint is the recommended follow-up.**
- **`pytz` is missing from `requirements.txt`** — `services/market_engine/`
  `validator.py` imports it, so the Market Engine fails to initialize
  (`Market Engine init error: No module named 'pytz'`). **Pre-existing and
  equally broken outside Docker**; surfaced by the clean-room build. Left unfixed
  as an application dependency change outside this sprint's scope. Should be
  fixed before any production deploy.
- **`WEB_CONCURRENCY` must remain 1** until PH2.8: the in-process APScheduler,
  heartbeat engine and in-memory WebSocket registry are not multi-process safe
  (N workers = N duplicate scheduled jobs; broadcasts reach only one worker's
  clients). Scale by replicas, not workers. The entrypoint warns loudly.
- Base image pinned by **tag**, not digest (picks up Debian patches on rebuild;
  digest pinning lands with image scanning in PH2.6). No vulnerability scanning
  yet (PH2.6). Built and verified on **linux/arm64** only — multi-arch in PH2.6.
- The existing root `docker-compose.yml` remains **development-oriented** (bind
  mounts, `--reload`, overrides the entrypoint) and is intentionally untouched;
  splitting dev/prod is PH2.3.

---

# Sprint PH1.12 — Security Certification (PHASE 1 EXIT GATE) — 2026-07-22

**Production Hardening PH1.12 complete — and with it, PH1.11. This is the final
Phase 1 sprint: the three PH1.11 verification residuals (F-1, F-2, F-3) are
fixed, the security verification checklist is executed, the security categories
are re-scored over the ≥ 8.0 gate, and Phase 1 (Production Security Hardening) is
formally CERTIFIED COMPLETE. Overall production deployment remains NO-GO — blocked
now by infrastructure (PH2) and QA (PH3), not by security.**

Added

- `backend/security/roles.py` (**F-1** — privilege escalation) — the single
  source of truth for the role taxonomy and who may grant each role.
  `ASSIGNABLE_ROLES` allowlists every legitimate `users.role` value;
  `validate_role_assignment(new_role, actor_role)` rejects unknown roles (400)
  and permits the admin-tier roles (`admin`, `super_admin`) **only** for a
  `super_admin` actor (403 otherwise). Closes the escalation path where
  `PUT /api/admin/users/{id}` accepted `role` as an unchecked passthrough field
  (any admin could promote anyone — including themselves — to admin/super_admin).
- `backend/security/identifiers.py` (**F-2** — unhandled id parsing) —
  `parse_object_id(value, resource)`, the single boundary where an untrusted
  identifier becomes a `bson.ObjectId`. Malformed input returns a clean **400**
  ("Invalid `<resource>` id", never echoing the value) instead of the previous
  uncaught `InvalidId` → HTTP **500**. Also fixes the surprising `ObjectId(None)`
  → *new random id* behavior by rejecting non-strings.
- `backend/tests/test_roles.py` + `backend/tests/test_identifiers.py` — **48
  hermetic tests**: role allowlist/least-privilege unit coverage plus end-to-end
  regression proving an `admin` cannot escalate a user or self-promote (403,
  stored role unchanged), a `super_admin` can, plan roles stay grantable by any
  admin, and a malformed user id returns 400 (not 500); id parsing across
  valid/passthrough/malformed/non-string/no-echo cases.
- `.github/dependabot.yml` (**F-3**) — weekly dependency-update PRs for `pip`
  (`/backend`), `npm` (`/frontend`), and `github-actions`; `docker` staged
  (commented) for PH2.1/2.2. Non-security minor/patch bumps grouped per
  ecosystem; security updates arrive as their own PRs.
- `backend/requirements-dev.txt` (**F-3 / finding M14**) — dev/CI tooling
  (`pytest`, `black`, `flake8`, `isort`, `mypy` + their exclusively-dev transitive
  deps, each verified dev-only via `pip show … Required-by`) split out of the
  runtime set; begins with `-r requirements.txt`. The production image now ships
  no dev tooling.
- `docs/security/PH1_CERTIFICATION.md` — the PH1 Security Certification Report:
  sprint inventory, F-1/F-2/F-3 detail, controls-verification matrix, OWASP Top 10
  posture, test summary, re-score, known limitations, and the signed certification
  decision.

Changed

- `backend/server.py` — wired in `parse_object_id` at every user-facing id
  boundary (admin user/ticket/feature-flag/announcement editors; trade
  update/exit/coaching/live-tip, trade-review, notification mark-read, paper
  close) and `validate_role_assignment` in `admin_update_user`. Trusted ids
  (verified JWT `sub`, `_id` read back from Mongo) intentionally stay raw.
- `.github/workflows/security-audit.yml` — audits **both** requirements files and
  runs `pip check` on the **runtime-only** install (which also proves the M14
  split is complete).
- `backend/requirements.txt` — dev tooling removed (moved to `requirements-dev.txt`).
- `backend/security/__init__.py` — documents the new `roles` and `identifiers`
  tenants.
- `.claude/SECRETS.md §7` — runtime/dev split, Dependabot cadence, and the
  severity triage SLA (critical blocks release · high 7d · medium 30d · low 90d).
- `.claude/TESTING.md` — new "Dependency Vulnerability Triage" section mirroring
  the SLA.
- `.claude/PRODUCTION_HARDENING.md` — §17 Security row **signed off**; readiness
  re-score (composite 4.2 → ~6.4; authn 9.0, API sec 8.5, secrets 8.5,
  observability 7.0).
- `.claude/PRODUCTION_ROADMAP.md` — PH1.11 and PH1.12 marked COMPLETE.
- `PRODUCTION_READINESS_REPORT.md` — PH1.12 update prepended (release decision,
  blocker status, final architecture, operational prerequisites, deployment/
  rollback/backup/recovery checklists); Sprint-12 baseline preserved below.
- `.claude/TASK.md` — PH1.11/PH1.12 marked complete; **Phase 1 marked COMPLETE**;
  next phase set to PH2.1.

Security posture / re-score

- No open **critical or high** security findings. F-1 (high) / F-2 (medium) /
  F-3 (medium) closed.
- **Authentication & Authorization 2.0 → 9.0**; **API & Transport Security
  3.0 → 8.5** — both clear the PH1.12 ≥ 8.0 exit gate.
- Verification checklist: no debug mode, no auth backdoors, no hardcoded/test
  secrets; cookies (HttpOnly always, Secure forced in prod) / CORS (no
  wildcard-with-credentials) / HSTS+CSP headers / CSRF / rate limiting / audit
  logging / fail-closed boot config all confirmed.

Tests

- Hermetic backend suite: **626 passed, 1 failed** — the one failure
  (`test_trading_engine::test_run_cycle_trails_and_books_targets`) is a
  pre-existing, documented engine-math test unrelated to this sprint (PH3.1).
- Legacy `requests`-based integration files (`test_backend.py`, `test_phase*.py`)
  require a live dev server; their failures/errors in a full run are environmental
  (ConnectionError), not regressions. Hermetic migration is PH3.1.

Decision

> **Phase 1 (Production Security Hardening): CERTIFIED COMPLETE.** Overall
> production deployment: **NO-GO** pending Phase 2 (Infrastructure & DevOps) and
> Phase 3 (Quality Assurance). Recommend transition to **PH2.1 — Backend
> Production Dockerfile**.

Deferred within PH1 (non-blocking, tracked): PH1.9 Real-Time/WebSocket Security
(R-15) and PH1.10b Admin Hardening & Session Management.

---

# Sprint PH1.10 — Audit Logging & Security Monitoring — 2026-07-22

**Production Hardening PH1.10 complete. Security-event observability is now
centralized: every security-relevant event flows through one module with one
taxonomy, one redacted schema, and one pluggable, SIEM-ready sink. Secrets can
never reach an audit log, and audit logging can never break a security flow.
Zero frontend, business-logic, JWT, or OAuth behavior change.**

Before this sprint there was no centralized audit log: security writes were
scattered across three ad-hoc writers with three record shapes (`log_auth_event`
→ `security_audit_logs`, `log_admin_action` → `admin_audit_logs`, broker `_audit`
→ `audit_logs`), each making its own "which fields are safe to log" judgement,
with no shared severity axis and no structured event model — an incident was a
cross-collection archaeology project.

Added

- `backend/security/audit.py` — the single source of truth for security-event
  logging. A **closed event taxonomy** (authentication / identity / session /
  security / administration) mapping every event to a `category` + default
  `severity` (info / notice / warning / critical); an unknown event fails safe to
  `security`/`warning`. A **versioned structured schema** (`schema_version=1`):
  event, category, severity, outcome, email, user_id, session_id, reason, ip,
  user_agent, request_id, target, redacted `details`, timestamp. **Recursive
  secret redaction** blanks any sensitive-keyed value (password, token, secret,
  authorization, code, state, csrf, hash, api_key, cookie, signature, …) before
  storage — defense-in-depth over careful call sites; depth-bounded against
  cyclic payloads. A **pluggable `AuditSink`** interface with a default composite
  of `MongoAuditSink` (durable `security_audit_logs`) + `LoggingAuditSink` (one
  JSON line/event — the SIEM/log-shipper seam); each sink isolated so one outage
  never starves the other. A **fail-safe `AuditLogger`**: emitting can never
  raise into the caller. Lazy DB provider (`audit.configure(lambda: db)`) so the
  live handle and the test `FakeDB` are both honored.
- `backend/tests/test_audit.py` — 20 hermetic tests: taxonomy classification,
  full-schema records, redaction (flat / nested / listed / depth-bounded),
  Mongo + logging + composite sinks, fail-safe emission, and live-app
  integration (login ±, registration, logout→session-revocation, invalid-JWT,
  refresh rotation, token-replay→CRITICAL, rate-limit trigger), plus a
  backward-compatibility test pinning the legacy `log_auth_event` record shape.

Changed

- `backend/server.py` — `log_auth_event` is now a thin backward-compatible
  facade over `security.audit` (identical signature; historical record fields are
  a strict subset of the new schema, so every existing caller, query, index, and
  test is unaffected). New fail-safe, control-flow-neutral audit hooks added to
  the auth surface: `login_success` / `login_failure`, `registration`,
  `session_created` (in `_issue_session`, covering login/register/OAuth),
  `logout` + `session_revoked`, `logout_all`, `refresh_rotation`,
  `token_replay_detected` vs. `invalid_refresh`, and `invalid_jwt` (only for
  genuinely tampered tokens — ordinary expiry is left unaudited to avoid noise).
  Startup extends `security_audit_logs` indexes (`category`, `severity`,
  `user_id`, `session_id`).
- `backend/security/csrf.py` — the middleware audits `csrf_validation_failure`
  before the 403 (lazy import, fail-safe).
- `backend/security/rate_limit.py` — `_trip` audits `rate_limit_triggered` at the
  single choke point covering inline and middleware limiters (lazy import,
  fail-safe).

Documentation

- `SECURITY_ARCHITECTURE.md` — new **§31b** (audit taxonomy, schema, redaction,
  sinks, fail-safe, instrumentation points); §32 plan, §33 rule 4, Architecture
  Summary, and Implementation Status updated.
- `SECURITY.md` — Audit Logging section expanded with the centralized model;
  Monitoring section gains operational alerting + retention guidance.
- `PRODUCTION_HARDENING.md`, `PRODUCTION_ROADMAP.md`, `TASK.md` — PH1.10 marked
  complete with the audit-event matrix.

Tests

- Full hermetic backend suite: **578 passed** (1 pre-existing, unrelated
  `test_trading_engine` failure deselected). All security suites (auth, OAuth,
  CSRF, rate-limit, JWT/sessions, recovery, cookies, headers) green — no
  regression from the facade migration.

---

# Sprint PH1.9 — Secrets & Supply Chain Security — 2026-07-22

**Production Hardening PH1.9 complete. Configuration is now centralized and
validated: a misconfigured or weakly-configured production refuses to boot, the
dependency set is fully pinned and continuously audited, and no hard-coded
secret remains in the repository. Zero frontend or business-logic change.**

This sprint delivers the roadmap's PH1.8 (Secrets & Environment Hardening)
content plus the supply-chain core of PH1.11, executed under the "PH1.9" label
(Identity Recovery had consumed the PH1.8 slot). Before it, the app read ~40 env
vars ad hoc with no boot-time validation (a missing `JWT_SECRET` only surfaced
at the first token operation), `docker-compose.yml` shipped a weak `JWT_SECRET`
fallback and a hard-coded n8n password, `.env.example` was git-ignored (so no
shareable template existed), and four dependencies floated on `>=` bounds.

Added

- `backend/security/secrets.py` — the single source of truth for the
  configuration surface. `SECRET_REGISTRY` declares every variable (category,
  `sensitive`, `required_in` environments, `min_length`, example). Boot-time
  `validate_config()` **fails closed**: it aggregates every problem into one
  value-free error and is called from `server.py` *before* the Mongo client or
  any router. Severity is environment-aware — the core trio (`MONGO_URL`,
  `DB_NAME`, `JWT_SECRET`) is fatal everywhere; production additionally makes
  fatal any missing required secret, a signing key < 32 chars, any
  placeholder/weak value, a half-configured OAuth or broker pair,
  `ENABLE_AUTO_LOGIN=true`, a weak `ADMIN_PASSWORD`, and the absence of any AI
  provider. No secret value is ever logged (`redact()`, presence-only summary).
  Reuses `security.cookies.is_production` so environment semantics never drift.
- `backend/.env.example` + `frontend/.env.example` — committed, placeholder-only
  templates. The backend template is **generated** from the registry by
  `backend/scripts/generate_env_example.py` (with a `--check` mode CI enforces),
  so code and template can never drift.
- `.github/workflows/security-audit.yml` — `pip-audit --strict` + `pip check`
  (backend), `npm audit` (frontend), `gitleaks` history scan + a tracked-`.env`
  guard, and the `.env.example` sync check. Runs on push/PR and weekly.
- `backend/scripts/audit_dependencies.py` — local `pip check` + `pip-audit`
  runner (degrades gracefully when pip-audit isn't installed).
- `.claude/SECRETS.md` — the secrets & supply-chain runbook: inventory,
  environment strategy, rotation policy (per secret class), dependency-update
  policy, accepted-advisory backlog, and leaked-credential incident response.
- `backend/tests/test_secrets.py` — 38 hermetic tests (env-aware validation,
  core-trio enforcement, cross-field invariants, no-secret-in-output,
  registry/example-sync integrity, accessors).

Changed

- `backend/server.py` — calls `security.secrets.validate_config()` immediately
  after `load_dotenv`; logs the presence-only summary + any warnings. This is
  the only server change (additive, non-breaking).
- `backend/requirements.txt` — now **fully exact-pinned**: locked the 4 floating
  `>=` bounds (aiohappyeyeballs, psutil, anthropic, litellm) and applied 7 in-pin
  security patches (aiohttp 3.13.5→3.14.1, cryptography 48.0.0→48.0.1, httplib2
  0.31.2→0.32.0, pillow 12.2.0→12.3.0, pyasn1 0.6.3→0.6.4, pymongo 4.5.0→4.6.3,
  python-multipart 0.0.29→0.0.31). Verified to co-resolve; starlette/litellm/
  ecdsa advisories deferred (framework-locked / AI-scope / unfixed) — see
  SECRETS.md §8.
- `docker-compose.yml` — removed the weak `JWT_SECRET` fallback (now required via
  `${JWT_SECRET:?…}`) and the hard-coded n8n password `alphapartner123` (now
  required `${N8N_BASIC_AUTH_PASSWORD:?…}`); added `APP_ENV`.
- `.gitignore` — negations (`!.env.example`, `!**/.env.example`) so example
  templates are committable while every real `.env` stays ignored.
- `backend/security/__init__.py` — documents the new `security.secrets` tenant.

Security notes

- **Fail-fast, fail-closed:** `APP_ENV=production` with any missing/weak required
  variable now exits before serving a single request, with a named-variable
  error listing all problems at once.
- **No secret in git history:** verified via `git log --all -S <value>` that no
  real provider key or `JWT_SECRET` was ever committed and no `.env` was ever
  tracked. One committed value existed — the n8n dev password `alphapartner123`
  in `docker-compose.yml` (5 commits) — now externalized; low severity
  (local-only editor basic-auth), documented in SECRETS.md §9.
- **Rotation reminder:** live provider keys currently in local `.env` files have
  existed in plaintext dev files and must be rotated before production launch
  (SECRETS.md §9).

Tests

- `backend/tests/test_secrets.py` — 38/38 passing. Full backend hermetic suite
  regression-checked (the pre-existing `requests`-based integration files and
  one pre-existing `test_trading_engine` failure are unrelated and unchanged by
  this sprint). Manual verification: prod-missing-secret aborts startup;
  valid-prod loads clean; the real dev `.env` boots.

---

# Sprint PH1.8 — Identity Recovery — 2026-07-22

**Production Hardening PH1.8 complete. The identity lifecycle is now
recoverable: users can verify their email, change their password, and reset a
forgotten one — each single-use, expiring, and safe against enumeration — with
zero frontend break and no change to the JWT/CSRF/rate-limit/OAuth layers.**

Before this sprint an account, once created, had no recovery path: no email
verification, no password change, no forgotten-password reset, and no way to
force-sign-out after a credential rotation. PH1.8 closes all four as a single
reusable `backend/security/recovery.py` module composed by new `/api/auth`
endpoints.

Added

- `backend/security/recovery.py` — the single source of truth for
  identity-recovery tokens. A **signed handle backed by an authoritative
  record**: the token handed to the user is `<token_id>.<HMAC(secret,
  "prefix|purpose|user_id|token_id")>` (unforgeable, bound to exactly one user +
  one purpose), while a `recovery_tokens` document carries `issued_at` /
  `expires_at` / `used_at` so **expiry and single-use are enforced
  authoritatively**. `consume()` burns a token with an atomic compare-and-set
  (`used_at: None → now`) — replay-safe. Issuing a fresh token invalidates the
  user's outstanding unused ones of that purpose (one live link at a time).
  - Purposes & lifetimes (env-overridable): **email verification 24h**
    (`RECOVERY_VERIFY_TTL_SECONDS`), **password reset 30 min**
    (`RECOVERY_RESET_TTL_SECONDS`).
  - HMAC key: `RECOVERY_SECRET` or the required `JWT_SECRET` (domain-separated by
    a versioned prefix — no weak default, fail-closed). Never logs a token.
- New `/api/auth` endpoints (all recovery logic centralized, never inline):
  - **`POST /verify-email`** — redeem a verification token (public; single-use).
  - **`POST /verify-email/request`** — resend the verification link
    (authenticated; generic response; no-op if already verified).
  - **`POST /forgot-password`** — start reset (public; **always** a generic
    response — no email enumeration; OAuth-only accounts silently skipped).
  - **`POST /reset-password`** — reset with a token + new password (public;
    enforces the PH1.5 policy; revokes every session; stamps
    `password_changed_at`).
  - **`POST /change-password`** — authenticated; requires the **current**
    password, rejects an unchanged one, enforces the PH1.5 policy, then revokes
    every session and stamps `password_changed_at` (signed out everywhere).
- `backend/tests/test_recovery.py` (28 tests) — hermetic: token
  mint/verify/consume, single-use/replay, expiry, purpose-binding, signature
  tamper, reissue invalidation; and the full endpoint matrix (verify success /
  expired / replay, forgot-password generic response, reset single-use / expiry /
  policy / session-revocation, change-password current-password /
  unchanged-password / policy / sign-out, and the untouched register→login→me
  lifecycle).

Changed

- **User model gains email-verification status:** `email_verified` (bool),
  `email_verified_at`, `verified_by` (`"email"` | `"google"`). New
  email/password registrations start **unverified** and are emailed a
  verification link (out-of-band via `BackgroundTasks`; a slow/failed mailer
  never delays sign-up). **Login is deliberately NOT blocked on this flag** —
  backward-compatible; it is the hook a future verified-only gate flips on.
- **Google OAuth accounts are verified on creation/link.** Google already
  asserts (and we already enforce) a verified email, so a Google-native or
  Google-linked account is marked `verified_by: "google"` with no separate
  verification email; pre-PH1.8 Google accounts self-heal the flag on next login.
- **`security.csrf`** default exempt paths extended with the three *public*
  recovery entrypoints (`/verify-email`, `/forgot-password`, `/reset-password`)
  — they carry their own single-use authorization or are anonymous, so they rely
  on no ambient cookie authority. The *authenticated* recovery actions
  (`change-password`, `verify-email/request`) stay CSRF-protected for cookie
  clients.
- **`services.email_service`** gains three branded templates:
  `EMAIL_VERIFICATION`, `PASSWORD_RESET`, `PASSWORD_CHANGED` (security
  confirmation).
- **Register response** gains the additive `email_verified` field so the SPA can
  surface a "verify your email" prompt; the rest of the contract is unchanged.
- Startup creates the `recovery_tokens` indexes (`token_id` unique,
  `(user_id, purpose)`, and a TTL index on `expires_at`).

Security properties

- **No enumeration:** forgot-password and verify-email/request return an
  identical generic message whether or not the account exists; recovery reads run
  through the existing rate limiter (`PASSWORD` policy, 5 / hour).
- **Single-use + replay-safe:** every recovery token is burned atomically on
  redemption; a replayed link is a generic 400.
- **Full session invalidation:** a reset OR change revokes every refresh family
  (`SessionStore.revoke_all_for_user`) and bumps `password_changed_at`, so
  outstanding access tokens also go stale on next use — the user re-logs in
  everywhere.

Out of scope (unchanged): JWT crypto, rate limiting, CSRF enforcement model,
OAuth login, cookie/header policy, trading engine, AI, frontend.

---

# Sprint PH1.7 — CSRF Protection & Rate Limiting — 2026-07-21

**Production Hardening PH1.7 complete. The two remaining abuse-surface gaps —
an unowned CSRF token layer and login-only rate limiting — are closed, without
any frontend change or public-API break.**

Before this sprint, cross-site state-changing requests were defended only by
`SameSite=Lax` (a real baseline, but no token layer), and rate limiting was a
single inline `db.login_attempts` lockout on `/api/auth/login`. PH1.7 adds a
signed, session-bound CSRF token layer and a centralized, progressive rate
limiter with a platform-wide flooding backstop — both as reusable
`backend/security/` modules.

Added

- `backend/security/csrf.py` — the single source of truth for CSRF. A **signed
  double-submit cookie bound to the session** (OWASP pattern): a non-HttpOnly
  `csrf_token` cookie carrying `<nonce>.<HMAC(secret, "prefix|sid|nonce")>`,
  echoed by the client in `X-CSRF-Token`.
  - **`CSRFMiddleware`** enforces on a request iff it is a mutating method, a
    non-exempt path, carries **no** `Authorization: Bearer` header, and is
    cookie-authenticated. Validation = header==cookie (double-submit) **and** the
    HMAC verifying against the cookie session's `sid` (binding). Failure → **403**
    (`code: CSRF_FAILED`), fail-closed.
  - **Bearer requests exempt by construction** — the SPA's `Authorization: Bearer`
    path cannot be forged cross-site and carries no ambient cookie authority, so
    enforcement targets exactly the cookie-only attack surface. This is what makes
    the rollout require **zero frontend changes**.
  - HMAC key: `CSRF_SECRET` or the required `JWT_SECRET` (domain-separated);
    cookie `Secure`/`SameSite`/`Domain` resolved through `security.cookies`.
- `backend/security/rate_limit.py` — the single limiter. Named per-endpoint
  policies, a pluggable `RateLimitStore` interface (shipped `MongoRateLimitStore`,
  Redis-ready), fixed-window counting, and **progressive lockout** with automatic
  expiry and `Retry-After`.
  - Policies (env-overridable via `RATE_LIMIT_<NAME>`): **login 5 / 15 min** per
    `ip:account` (failures only; escalating lockout), **register 5 / hour** per IP,
    **refresh 20 / min** per session, **password 5 / hour**, **authenticated API
    120 / min** per user, **public API 60 / min** per IP.
  - **`RateLimitMiddleware`** — platform-wide flooding backstop over all `/api`
    traffic (per-user when authenticated, per-IP otherwise); emits
    `X-RateLimit-*`; a storage error fails **open** (logged) so the throttle can
    never take the API down.
- `backend/tests/test_csrf.py` (18 tests) and `backend/tests/test_rate_limit.py`
  (26 tests) — hermetic: token mint/verify/bind, every middleware
  exempt/enforce/reject path, store/limiter semantics, lockout + escalation,
  `Retry-After`, and the real auth-endpoint integrations.

Changed

- `backend/server.py`:
  - `_issue_session` and `/refresh` now plant/re-mint the CSRF cookie;
    `logout`/`logout-all` clear it.
  - `login` replaces the inline `login_attempts` block with the centralized
    limiter (`peek` → `record_failure` → `reset`); `register` and `refresh` gain
    inline limits. Observable lockout behavior is preserved byte-for-byte.
  - Middleware wired: `apply_csrf_protection` + `apply_rate_limiting` registered
    **before** CORS/headers so a 403/429 still carries CORS + security headers
    (execution order: Security Headers → CORS → Rate Limiter → CSRF → route).
  - Startup drops the `login_attempts` index; adds `rate_limits` indexes
    (compound `(key, kind, window_start)` + TTL on `expires_at`).
- `backend/security/__init__.py` — tenant index lists `csrf` and `rate_limit`.
- `backend/tests/test_password_policy.py` — two tests that asserted the internal
  `login_attempts` collection now assert the new limiter's observable behavior
  (`rate_limits`); the login-compatibility guarantees are unchanged.

Migration

- **No data migration, no API break.** The `login_attempts` collection is simply
  no longer written (a Mongo TTL/manual drop can retire it). Existing Bearer-based
  clients are unaffected by CSRF (exempt); a future cookie-only client reads the
  `csrf_token` cookie and sets `X-CSRF-Token`. All limits are env-tunable.

Threat-model rows "Cross-site state-changing request via cookie auth (CSRF
proper)", "Credential stuffing / brute force", and "Endpoint flooding / token
abuse" move to ✅ Closed. Remaining PH1 work: PH1.8 (secrets/env validator),
PH1.9 (WebSocket authorization), PH1.10–PH1.12.

---

# Sprint PH1.6 — JWT Lifecycle & Session Security — 2026-07-20

**Production Hardening PH1.6 complete. The two highest-value open authentication
risks — long-lived access tokens (R-06) and refresh-token replay — are closed.**

Before this sprint, access tokens lived 24 hours, refresh tokens never rotated
or revoked, and logout only deleted cookies (the JWTs stayed cryptographically
valid until natural expiry). A stolen token was usable for a day; a captured
refresh token for a week, undetectably. PH1.6 centralizes all JWT logic, shortens
the access token to 15 minutes, rotates refresh tokens on every use with theft
detection, and adds a durable server-side revocation store — without changing any
public API contract.

Added

- `backend/security/jwt.py` — the single source of truth for JWT issuance and
  verification (pure crypto, no FastAPI/DB). The only place a token is encoded or
  decoded.
  - **Hardened claim set on every token:** `iat`, `jti` (unique id — the handle
    the session store rotates/revokes), `aud`, `iss`, `sid` (owning session), and
    `ver` (token schema version), alongside the existing `sub`/`email`/`type`/`exp`.
  - **Strict, fail-closed verification** (`decode_token`) — validates signature,
    `exp`, `aud`, `iss`, requires every claim, and checks `type`/`ver`. Raises
    typed, framework-neutral `TokenExpired`/`TokenInvalid` (never a raw `pyjwt`
    error), which the web layer maps to a generic 401.
  - **Configurable lifetimes** — `JWT_ACCESS_TTL_SECONDS` (default **900 / 15 min**)
    and `JWT_REFRESH_TTL_SECONDS` (default **604800 / 7 days**), plus `JWT_ISSUER` /
    `JWT_AUDIENCE`. `TOKEN_VERSION` is a pinned-in-code global kill-switch.
  - **`password_changed_at` support** (`token_issued_before`) — the anchor a future
    password change / forced-logout uses to invalidate every outstanding token by
    `iat`, for both access and refresh.
- `backend/security/sessions.py` — `SessionStore`, the DB-backed session (refresh-
  token family) store. One MongoDB `sessions` document per login/device.
  - **Rotation on every refresh** — the presented refresh token is single-use; a
    new `jti` becomes current and the old one is dead.
  - **Reuse detection** — replaying an already-rotated refresh token (its `jti` no
    longer current) is treated as theft and **revokes the entire family**, so both
    attacker and victim are forced to re-login (closes refresh-replay).
  - **Revocation** — `revoke` (single session / logout) and `revoke_all_for_user`
    (logout-all-devices); durable, TTL-reaped at `expires_at`.
  - **PH1.10 groundwork** — captures `user_agent`, `ip`, created/last-used
    timestamps, and exposes `list_for_user` for the future active-sessions screen.
- `backend/tests/test_jwt_sessions.py` — 34 hermetic tests: claim set, every
  rejection path (expired / wrong-aud / wrong-iss / bad-signature / wrong-type /
  stale-version / missing-claim / garbage), rotation, reuse→family-revoke, revoke,
  revoke-all, `password_changed_at`, and the full HTTP lifecycle.
- `POST /api/auth/logout-all` — authenticated "sign out of all devices" endpoint.

Changed

- `backend/server.py` — `create_access_token`/`get_current_user`/`refresh`/`logout`
  now delegate to `security.jwt` + `security.sessions`; the inline `pyjwt`
  encode/decode and the old 24h/7d helpers are gone. Login/register/OAuth open a
  session via a shared `_issue_session` helper (captures device/IP). Refresh
  rotates **both** cookies. Startup provisions `sessions` indexes (unique
  `session_id`, `user_id`, TTL on `expires_at`). `import jwt as pyjwt` removed.
- `backend/security/__init__.py` — tenant index lists `jwt` and `sessions`.
- `backend/tests/test_cookie_security.py` — the PH1.3 test that asserted refresh
  does *not* rotate the refresh cookie (explicitly deferred to PH1.6) now asserts
  the rotated refresh cookie carries the hardened flags.

Migration

- **Clean cutover.** Strict validation rejects pre-PH1.6 tokens (no `aud`/`ver`),
  so active users re-authenticate once via the normal 401 → login flow on deploy.
  No data migration. `cookies.py` (cookie `Max-Age`) is unchanged — the access
  cookie's 24h Max-Age harmlessly outlives the 15-min JWT (expired token →
  silent refresh); aligning them is a cosmetic PH1.3-owned follow-up.

Risk R-06 closed. Threat-model rows "Stolen long-lived access token" and "Refresh
token replay" move to ✅ Closed. Note: the roadmap's placeholder `tokens.py` was
realized as two cohesive modules (`jwt.py` pure crypto + `sessions.py` stateful
store) and refresh defaults to 7 days (env-tunable to the SECURITY.md 30-day
target); both deviations are recorded in PRODUCTION_ROADMAP.md PH1.6.

---

# Sprint PH1.4b — HTTP Security Headers — 2026-07-20

**Production Hardening PH1.4b complete. The "no security headers" gap (flagged in
the PH0 audit and de-scoped from the CORS-only PH1.4) is closed.**

Before this sprint the API emitted **no** security response headers — every
response could be framed, MIME-sniffed, and leaked referrers, with no transport
pinning and no content policy. PH1.4b adds the full defensive header set in one
centralized, environment-driven place, wired *after* CORS so even CORS
preflight and rejected-origin responses carry the headers. API contracts and
payloads are unchanged; only response headers are added.

Added

- `backend/security/headers.py` — the single source of truth for HTTP response
  security headers. No security header may be set anywhere else.
  - **Middleware** (`SecurityHeadersMiddleware`, `apply_security_headers`) — a
    pure-ASGI middleware (not `BaseHTTPMiddleware`) chosen so it never buffers
    the body (safe for streaming/SSE), touches only the `http` scope
    (WebSocket upgrades pass through), and **enforces** its values (overwriting
    any inner-handler value so the posture cannot be weakened downstream).
  - **Emitted on every response:** `X-Content-Type-Options: nosniff`,
    `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
    a locked-down `Permissions-Policy` (camera/mic/geolocation/USB/… disabled),
    `Cross-Origin-Opener-Policy: same-origin`,
    `Cross-Origin-Resource-Policy: same-origin`, `X-XSS-Protection: 0` (the
    deprecated, buggy legacy auditor neutralized), and a strict
    `Content-Security-Policy` (`default-src 'none'; base-uri 'none';
    form-action 'none'; frame-ancestors 'none'`) — **no `unsafe-inline` /
    `unsafe-eval` anywhere.**
  - **Conditional:** `Strict-Transport-Security`
    (`max-age=63072000; includeSubDomains`) is emitted **only** over HTTPS or in
    production (honors `X-Forwarded-Proto` behind a TLS-terminating proxy;
    `preload` opt-in) so a plain-HTTP dev origin never pins itself.
    `Cross-Origin-Embedder-Policy: require-corp` is implemented but **opt-in**
    (`CROSS_ORIGIN_EMBEDDER_POLICY`) — it protects the API's own JSON not at all
    yet would break same-origin HTML tooling (Swagger UI) pulling cross-origin
    subresources without CORP.
  - **Environment-driven & nonce-capable:** every header value is overridable
    via environment variable (`CONTENT_SECURITY_POLICY`, `PERMISSIONS_POLICY`,
    `REFERRER_POLICY`, `X_FRAME_OPTIONS`, `CROSS_ORIGIN_*`, and the `HSTS_*`
    family). A `{nonce}` placeholder in the CSP is replaced per request with a
    fresh `secrets.token_urlsafe(16)` nonce, also exposed on
    `request.state.csp_nonce` for a future HTML handler to stamp onto
    `<script nonce=…>` tags.
- `backend/tests/test_security_headers.py` — 35 hermetic tests (no network, no
  Mongo): HSTS enablement/value and HTTPS/production gating, the strict default
  CSP and its nonce substitution, the cross-origin isolation family, every
  environment override, and real wire behavior through the middleware on
  success, error, CORS-preflight, and nonce-based responses.

Changed

- `backend/server.py` — wires `apply_security_headers(app)` immediately after
  `apply_cors(app)` (so headers wrap CORS responses too). No other change.
- `backend/security/__init__.py` — records `security.headers` in the tenant index.

Notes

- **CORP is safe with the credentialed CORS frontend:** `Cross-Origin-Resource-Policy`
  only governs *no-cors* cross-origin loads, so the frontend's `mode: cors`
  requests (governed by `security.cors`) are unaffected while the API can no
  longer be embedded as an opaque subresource.
- **Swagger UI (`/docs`) and any HTML served from the API origin** will be
  restricted by the strict `default-src 'none'` CSP; a deployment that needs it
  relaxes `CONTENT_SECURITY_POLICY` (or, preferably, disables docs in
  production). No production JSON API endpoint is affected.

Verification

- `pytest backend/tests/test_security_headers.py backend/tests/test_cors_hardening.py
  backend/tests/test_cookie_security.py backend/tests/test_password_policy.py`
  → **128 passed** (33 new + 95 regression). Manual: real-app smoke check on
  `/api` in production mode confirmed all headers present (including HSTS) and
  the CORS 400 rejection still carries `X-Frame-Options`.

Scope note

- Out of scope and untouched per the sprint definition: JWT/refresh, cookies,
  OAuth, password policy, CSRF, rate limiting, email verification,
  infrastructure/Docker, logging, database, and the frontend. Deferred:
  request-scoped nonce propagation into rendered HTML templates (the header/state
  plumbing exists now; no HTML is rendered by the API yet).

---

# Sprint PH1.5 — Password Policy & Account Protection — 2026-07-19

**Production Hardening PH1.5 complete. Finding H10 (password half) closed; risk
R-05 partially mitigated (password-policy half — the rate-limiting half remains
PH1.7).**

Replaced the accept-anything password handling (`password: str`, no validator,
implicit bcrypt cost) with a production-grade, centralized password policy.
Enforcement is at the model layer, so weak passwords are rejected with 422
before they ever reach hashing. Existing users, login, and OAuth are unchanged:
the policy applies to **new** passwords only, and the register/login API
contracts (payloads, success shapes, generic 401, `ip:email` lockout) are
byte-for-byte preserved.

Added

- `backend/security/passwords.py` — the single source of truth for password
  policy, hashing, and verification. No password may be validated, hashed, or
  verified outside this module.
  - **Policy** (`validate_new_password`, returns every violated rule at once):
    12–64 characters (and ≤72 UTF-8 bytes — the bcrypt truncation boundary);
    uppercase + lowercase + number + special character required; rejects
    common passwords, email-/name-derived passwords, repeated-character
    passwords (<5 unique chars), and sequential runs (alphabet/digits/qwerty
    rows, forward or reversed). Leading/trailing whitespace is normalized away
    before validation *and* hashing.
  - **Hashing** — bcrypt with an explicit, pinned cost factor
    (`BCRYPT_ROUNDS = 12`); previously the cost was the silent library default.
  - **Verification** — constant-time (`bcrypt.checkpw`) and never raises:
    empty/malformed stored hashes return `False` after a dummy-hash comparison,
    which also **timing-equalizes** login failures (unknown email, OAuth-only
    account, and wrong password all cost one bcrypt comparison).
- `backend/security/data/common_passwords.txt` — bundled, curated common-password
  blocklist (~450 lowercase entries; padding-resistant matching strips trailing
  digits/punctuation, so `Monkey987654!!` still matches `monkey`). No new
  dependencies.
- `backend/tests/test_password_policy.py` — 40 hermetic tests: every policy rule
  (boundaries, character classes, common/sequential/repeated/identity-derived,
  whitespace, multibyte length), hashing primitives (explicit cost, round-trip,
  never-raises, delegation), register-endpoint enforcement (422 + no user
  created, clean error contract, unchanged success shape), and the sprint's
  compatibility guarantees (legacy weak-password login works, OAuth-native
  401-not-500, indistinguishable failures, lockout preserved/cleared).

Changed

- `backend/models.py` — `UserCreate` gained a `model_validator` that normalizes
  the password and enforces the policy (422 with actionable rule messages;
  cross-field checks against the user's own email/name). `UserLogin` is
  deliberately unvalidated so existing accounts keep working.
- `backend/server.py` — removed the inline `hash_password`/`verify_password`
  definitions (and the now-unused `bcrypt` import); both are imported from
  `security.passwords`. Login now always runs exactly one bcrypt comparison
  (timing-equalization) — this also fixed a real bug where password login
  against a Google-OAuth-native account (`password_hash: ""`) raised
  `ValueError` → 500 instead of the generic 401. Added a sanitizing
  `RequestValidationError` handler: 422 bodies now carry only `loc`/`msg`/`type`
  — FastAPI's default handler echoed the submitted input (including raw
  passwords) back in every validation error.
- `backend/scripts/seed_dev_admin.py` — hashes via `security.passwords`
  (consistent cost factor; still dev-only, still no policy on seeded creds).
- `backend/tests/_fakedb.py` — `update_one` now supports `$inc` (match and
  upsert), making the login-lockout counter hermetically testable for the
  first time.
- `frontend/src/pages/Register.jsx` — client-side minimum raised 6 → 12 to
  mirror the server policy; full rule feedback comes from the API's 422
  messages, which the existing `formatApiError` already renders.

Security outcome

- No weak password can enter the system through any current registration path;
  policy logic exists in exactly one module (no per-endpoint drift).
- bcrypt cost is an explicit, reviewed constant; verification can no longer
  500 on hostile or legacy data.
- Login failures are generic **and** timing-equalized; validation errors no
  longer reflect submitted values. Password-hash exposure re-verified: no
  endpoint returns `password_hash` (covered by tests).
- Brute-force lockout (5 attempts / 15 min per `ip:email`) preserved unchanged
  and now under test.

Not in scope (deferred, unchanged)

- Password change endpoint and password reset flow (reviewed: neither exists;
  no reset tokens are generated anywhere) — deferred with `EmailStr` and email
  verification to an unscheduled PH1.5b (SMTP provider decision OR-6 moves with
  them). Platform-wide rate limiting remains PH1.7; JWT lifetime/rotation and
  session revocation remain PH1.6. Audit-logging of password-login events
  remains a tracked gap (SECURITY_ARCHITECTURE.md §22, PH1.6/PH1.7 candidate).

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

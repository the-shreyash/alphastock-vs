# Disaster Recovery & Business Continuity

Authoritative document for how StockAssist AI recovers from infrastructure
failure: what can be lost, how long each recovery takes, the exact commands, and
how a recovery is *proved* rather than assumed. Delivered by **PH2.10**.

> **A backup strategy answers "can we get the data back?". A disaster recovery
> plan answers "can we get the *business* back, and how long will it be down?"**
> Those are different questions. PH2.9 answered the first one and measured it.
> This document answers the second, and everything in it is arranged around the
> fact that the answer is written *before* the incident, by people who are
> calm, and executed *during* it, by one person who is not.

---

## 1. What this gives you, and what it deliberately does not

**It gives you**

* Ten runbooks covering every failure mode this deployment can actually have —
  each with diagnosis, recovery, what to do when the recovery fails, and a
  verification step that is a command rather than an opinion.
* A **layered verification tool** (`scripts/dr/dr_verify.sh`) that answers "is
  it back?" at four levels instead of the one that is easy to check.
* A **verified deployment rollback** with a deployment ledger, a precondition
  that refuses to stop the running version until the replacement image is
  confirmed present, and an automatic revert when the rollback is also broken.
* Recovery objectives that are **measured**, with the human time named
  separately from the mechanical time — because the human time is nearly all
  of it.
* A drill schedule, a postmortem template, and an escalation matrix.

**It deliberately does not give you**

* **High availability.** Every tier here is a single instance on a single host.
  This document describes *recovery*, which takes minutes to hours; it does not
  describe *failover*, which takes seconds. §14 is the honest migration path.
* **Automated failover or self-healing** beyond Docker's `restart:
  unless-stopped`. Every runbook below has a human in it, on purpose — see
  §5.3.
* **Cloud/multi-region DR.** Out of scope for PH2.10 by design.
* **A tested off-host restore.** The off-host copy is still documented rather
  than implemented (BACKUP_AND_RESTORE.md L2), and it is the largest single
  risk in this plan. §12 L1.

---

## 2. What can be lost, and what that costs

Recovery planning starts with an inventory, because the instinct to "back
everything up" produces a plan that is expensive, slow, and still missing the
one thing that mattered.

| Tier | Where it lives | Lost when | Reconstructible from | Class |
|---|---|---|---|---|
| **Application data** | `mongo_data` volume | volume loss, corruption, bad migration, `dropDatabase` | **nothing** | **CRITICAL** |
| **Secrets & keys** | `secrets/`, `.env`, `production.env` | host loss, accidental rotation | **nothing** (see §7.8) | **CRITICAL** |
| **Application code & config** | git | never, if pushed | the remote repository | LOW |
| **Container images** | Docker's local image store | host loss, `docker image prune` | rebuild from a commit | MEDIUM |
| **Cache & realtime** | `redis_data` volume | volume loss, restart | the providers, on demand | **NONE** |
| **Logs** | `backend_logs` volume + stdout | `down -v`, host loss | nothing — but they are evidence, not state | MEDIUM |
| **Uploads** | `backend_uploads` (declared, not yet mounted) | volume loss | nothing, once it ships | CRITICAL *(future)* |

Two rows carry the whole plan:

**The application database and the secrets are equally critical, and losing
either one alone loses both.** A restored database that cannot be decrypted is
not a recovery — `BROKER_TOKEN_KEY` is not a credential you can reissue, it is
the key to ciphertext already sitting in the database (BACKUP_AND_RESTORE.md
§10). Any recovery procedure that restores one without the other is a procedure
that has not been thought through.

**Redis is class NONE and that is a design property, not luck.** Sessions, rate
limits and the audit trail were deliberately put in MongoDB so that Redis could
stay disposable (BACKUP_AND_RESTORE.md §6). It is what makes R3 a thirty-second
runbook instead of a data-loss event — and it stops being true the moment
something durable is written to Redis, which is why the no-TTL tripwire is in
the monthly checklist.

---

## 3. Recovery architecture

```
                              INCIDENT
                                 │
                    ┌────────────▼────────────┐
                    │       DETECTION         │   uptime check · alerts ·
                    │   "something is wrong"  │   a user · a failed cron mail
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │       DIAGNOSIS         │   ./scripts/dr/dr_verify.sh
                    │   "which layer failed?" │   → the failing LAYER names
                    └────────────┬────────────┘      the runbook
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
     ┌────────▼───────┐ ┌────────▼───────┐ ┌────────▼───────┐
     │ APPLICATION    │ │ DATA           │ │ HOST           │
     │ R1 rollback    │ │ R4 restore     │ │ R6 volume loss │
     │ R2 restart     │ │ R8 config      │ │ R7 total loss  │
     │ R3 redis       │ │                │ │                │
     └────────┬───────┘ └────────┬───────┘ └────────┬───────┘
              └──────────────────┼──────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │      VERIFICATION       │   dr_verify.sh --level full
                    │  "is it back — really?" │   4 layers, no inference
                    └────────────┬────────────┘
                                 │
                          ┌──────┴──────┐
                     FAIL │             │ PASS
                          │             │
              ┌───────────▼──┐   ┌──────▼──────────────────┐
              │  ESCALATE §5 │   │   NORMAL OPERATIONS     │
              │  R5 / §6     │   │   + postmortem §10      │
              └──────────────┘   └─────────────────────────┘
```

The shape that matters: **diagnosis is a tool, not a conversation, and
verification is the same tool.** The most common way a recovery goes wrong is
that the operator forms a theory in the first sixty seconds and spends the next
forty minutes confirming it. Running one command that reports four layers
independently is the cheapest available defence against that.

### The files

| File | Role |
|---|---|
| `scripts/dr/dr_verify.sh` | Layered diagnosis **and** verification. Safe to run against a broken system — that is what it is for. |
| `scripts/dr/deploy_rollback.sh` | Deployment ledger (`record`/`list`/`current`) and verified rollback with automatic revert. |
| `scripts/backup/*` | Backup, restore, verification (PH2.9). Every data runbook below is a wrapper around these. |
| `docs/runbooks/POSTMORTEM_TEMPLATE.md` | The blameless writeup that closes an incident. |
| `backend/tests/test_disaster_recovery.py` | 41 hermetic tests asserting the safety properties of the two scripts above. |

---

## 4. Recovery objectives

### 4.1 The targets

| | Target | Basis |
|---|---|---|
| **RPO** | **≤ 24 h** | One full backup nightly at 03:15 UTC. A total-loss incident at 03:00 loses ~24 h of writes; one at 03:30 loses ~15 minutes. This is a *worst case*, not an average. |
| **RTO** | **≤ 4 h** | Total loss of the host, rebuilt from off-host artifacts. Dominated by human and provisioning time — see the budget below. |
| **RTO (application-only)** | **≤ 15 min** | A failed deployment, a crashed container, a Redis loss. No data movement. |

These match `.claude/PRODUCTION_HARDENING.md` §11.

### 4.2 Where the four hours actually go

An RTO built from mechanical timings is always wrong in the optimistic
direction. This one is built from the whole path, with the measured parts marked:

| Phase | Budget | Note |
|---|---|---|
| Detection | 0–30 min | **The weakest link.** Until PH2.10's alerting lands, detection is a human noticing. With an uptime check: ~2 min. |
| Decision & comms | 10 min | Declare, notify, pick a runbook. Skipped under pressure; costs more than it saves. |
| Provision a replacement host | 30–60 min | Provider-dependent. Docker + compose + `git clone`. |
| Fetch artifacts from off-host storage | 5–30 min | Bandwidth-bound. **Unverified — §12 L1.** |
| Restore configuration & secrets | ~5 min | **Measured: 0.17 s** to decrypt and unpack 14 files; the rest is a human checking them. |
| Start the data tier | 2 min | `docker compose up -d mongo redis` |
| Restore MongoDB | **measured** | **4.5 s** for the real 21-collection dev database; **~2.5 min at 1 GB**, **~22 min at 10 GB** (BACKUP_AND_RESTORE.md §8.1). |
| Start the application | 2 min | Includes a ~20-index boot; the startup probe covers it. |
| Verification | **1.1 s measured** | `dr_verify.sh --level full` — plus ~10 min of human sanity checking. |
| **Total** | **~2–4 h** | The mechanical work is **under five minutes**. Everything else is people and provisioning. |

**The lesson to take from that table is not "we are fast".** It is that
optimising the restore is pointless and optimising *detection* is worth an hour.
That is why PH2.10's alerting (roadmap) matters more to RTO than any change to
these scripts would.

### 4.3 Assumptions this plan depends on

Every one of these is a way the plan fails silently if it stops being true.
They are checked in §9's drill, not assumed.

| # | Assumption | If false |
|---|---|---|
| A1 | Backups exist **off this host** and are reachable without it | Host loss = total data loss. **Not yet verified — §12 L1.** |
| A2 | The backup passphrase is in an offline escrow reachable by **at least two people** | Every artifact is permanently unreadable. Unrecoverable. |
| A3 | The git remote is reachable and contains the deployed revision | Code and config must be reconstructed by hand. |
| A4 | A replacement host can be provisioned within the RTO | RTO is provider-bound, not plan-bound. |
| A5 | At least one person knows this document exists and where the escrow is | The plan is a file, not a capability. |
| A6 | The previous container image is still on the host **or** rebuildable from a recorded commit | Rollback is impossible; only roll-forward remains (§8.2). |
| A7 | Recovery credentials (`MONGO_ROOT_*`) are available to the operator | The restore cannot authenticate. |

### 4.4 Business continuity assumptions

* **This is a single-host deployment with no failover.** Any recovery in this
  document is an *outage*, not a degradation. Users see errors, not slowness.
* **Market data is refetchable; user data is not.** During recovery, the
  Market Gateway's providers are unaffected — a recovered system is fully
  current on market data within minutes and is missing only whatever user
  writes fall inside the RPO window.
* **The blast radius of an RPO event is user-visible and must be communicated.**
  Up to 24 h of journal entries, trades, portfolio edits and notifications can
  be gone. Users must be told which window, not that "some data may be
  affected".
* **No financial reconciliation guarantee across collections.** `mongodump`
  against a standalone mongod is per-collection consistent only
  (BACKUP_AND_RESTORE.md §5.3). A restore is "approximately 03:15 UTC", not a
  transaction boundary. Do not use a restored database to settle a dispute
  about an exact sequence of writes without checking §14's PITR path first.
* **Legal/regulatory retention lives in MongoDB.** The security audit trail is
  in the database and therefore inside the backup; the log-file copy is a
  convenience, not the record of authority (LOGGING.md L5).

---

## 5. Severity, escalation and roles

### 5.1 Severity

| Sev | Meaning | Examples | Response |
|---|---|---|---|
| **SEV-1** | Total outage or **any** data loss/exposure | host lost, database corrupt, restore needed, secrets leaked | Immediate. Wake people. Declare. |
| **SEV-2** | Degraded but serving | one tier down (Redis), a bad deployment rolled back, elevated errors | Within the hour, business hours or not. |
| **SEV-3** | Contained, no user impact | a failed backup job, one restart-loop that recovered, a full disk warning | Next business day. **Still gets a ticket** — this is where SEV-1s come from. |

Two rules that exist because they get broken:

1. **Any suspected data loss is SEV-1 regardless of how few users it touched.**
   "Only one account" is a severity assessment made before anyone knew.
2. **Downgrading severity requires evidence, not the passage of time.** A
   quiet system is not a recovered one until §9 verification says so.

### 5.2 Escalation matrix

Roles, not names — a matrix listing people goes stale in a quarter. Fill the
contact column in your own operational store, not in this repository.

| Time / trigger | Escalate to | Why |
|---|---|---|
| T+0 | **On-call engineer** | Declares severity, opens the incident log, runs the runbook. |
| T+30 min on a SEV-1, or any restore decision | **Engineering lead** | A restore is a decision to *accept* data loss. That decision is not the on-call engineer's to make alone at 03:00. |
| T+60 min on a SEV-1, or any data-loss/exposure confirmation | **CTO / founder** | User communication, regulatory exposure, the call to stay down longer for a better recovery. |
| Host/network unreachable, storage failure | **Hosting provider support** | Open the ticket *in parallel* with recovery, never instead of it. |
| Suspected compromise (R9) | **Security lead + CTO immediately** | Recovery steps change completely: preserve evidence, rotate everything, rebuild clean (§7.9). |
| Market-hours outage > 15 min | **Whoever owns user comms** | Users of a trading platform during a session need to be told, fast. |

### 5.3 Why every runbook has a human in it

There is no automatic restore, and there will not be one. Automated recovery is
correct when the failure mode is unambiguous and the action is cheap
(`restart: unless-stopped` is exactly that, and it is enabled). It is wrong when
the action is *destructive and irreversible*, because then the automation must
be right about the diagnosis, and an automation that restores from a backup
because it misread a symptom has converted a five-minute blip into a day of lost
writes. **A restore is a decision to accept data loss. Deciding that is a human
job.**

---

## 6. Common first moves (do these before any runbook)

```bash
cd /srv/stockassist

# 1. WHAT is broken — four layers, independently, ~1 second.
./scripts/dr/dr_verify.sh --level full

# 2. WHAT changed — the answer is "a deployment" far more often than anything else.
./scripts/dr/deploy_rollback.sh current
./scripts/dr/deploy_rollback.sh list

# 3. WHAT the system is saying.
docker compose logs --since 30m --tail 200 backend
docker compose ps
```

Then **write down the start time and the symptom** before touching anything.
Ten seconds now; it is the difference between a postmortem and an anecdote.

**The one thing not to do first:** restarting everything. `docker compose
restart` is the reflex, it destroys the evidence of what happened, and it fixes
a class of problem that mostly does not exist here (`restart: unless-stopped`
has already tried). Diagnose, *then* act.

---

## 7. Runbooks

Each runbook is: **symptom → diagnosis → recovery → if the recovery fails →
verification**. Every command is executable as written from the repository root
on the Docker host.

---

### R1 — Failed deployment

**Symptom.** Errors, 5xx, or a failed readiness check that began within minutes
of a deploy. `dr_verify.sh` shows layer 4 failing while layers 1–3 pass.

**Diagnosis.**

```bash
./scripts/dr/dr_verify.sh --level full        # which layer?
./scripts/dr/deploy_rollback.sh current       # what is running?
./scripts/dr/deploy_rollback.sh list          # what was running before?
docker compose logs --tail 200 backend
```

If layer 3 (data) is also failing, this is **not** R1 — a deployment does not
break MongoDB. Go to R4.

**Recovery.**

```bash
./scripts/dr/deploy_rollback.sh rollback --previous
```

That single command: resolves the previous *different* tag from the ledger,
**refuses to proceed unless that image is on this host**, records the
roll-forward point, rewrites `BACKEND_IMAGE_TAG` atomically, recreates only the
backend (`--no-deps`), waits for health, and **reverts automatically** if the
older version is not healthy either.

**⚠ Before you run it, answer the one question it cannot:** did the new version
change the database in a way the old one does not understand — a migration, a
new required field, a renamed collection? If yes, rolling the image back is not
a rollback; the old code meets a schema it has never seen. Use R4 (restore) or
roll *forward* with a fix. The script asks you this and then requires you to
type the tag.

**If the recovery fails.** The script reverts to what was running and tells you
so. Both versions are now suspect: go to **R5**.

**Verification.**

```bash
./scripts/dr/dr_verify.sh --level full --expect-version <previous-version>
```

---

### R2 — Backend container down or restart-looping

**Symptom.** Liveness fails; `docker compose ps` shows the backend restarting,
exited, or "up" with a climbing restart count (`dr_verify` reports that
explicitly).

**Diagnosis.**

```bash
docker compose ps
docker compose logs --tail 200 backend
docker inspect --format '{{.State.ExitCode}} {{.RestartCount}}' "$(docker compose ps -q backend)"
```

Read the exit code before restarting anything:

| Exit | Almost always means |
|---|---|
| `1` + a config error in the logs | Fail-closed boot validation (`security/secrets.py`) — a secret is missing or invalid. **This is the system working.** Fix the value, do not bypass it. → R8 |
| `137` | OOM-killed. Raise the memory limit or reduce `WEB_CONCURRENCY`; a restart alone will repeat it. |
| `0`, repeatedly | The process is exiting cleanly — an entrypoint or command problem, usually after an image change. → R1 |

**Recovery.**

```bash
docker compose up -d --no-deps --force-recreate backend
docker compose logs -f backend        # watch it boot, do not walk away
```

**If the recovery fails.** If it restart-loops on the same error, this is a
configuration or image problem, not a container problem → R8 or R1.

**Verification.** `./scripts/dr/dr_verify.sh --level full`

---

### R3 — Redis failure or data loss

**Symptom.** Readiness returns 503 with the Redis probe failing, or realtime
updates stop on one replica while HTTP keeps working, or
`redis_circuit_state` is open.

**Diagnosis.**

```bash
docker compose ps redis
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning PING
curl -fsS -H "Authorization: Bearer $METRICS_TOKEN" localhost:8000/api/diagnostics/redis
```

`/api/diagnostics/redis` is the one that matters: it reports **per-channel
subscriber state**, which no `PING` can. A Redis that answers PING while the
Pub/Sub subscriber is dead looks completely healthy and delivers no realtime
events (the PH2.7 defect).

**Recovery.** There is no data to recover — everything in Redis is
reconstructible cache (BACKUP_AND_RESTORE.md §6).

```bash
docker compose up -d redis                                  # starts, empty if the volume was lost
docker compose exec redis redis-cli INFO persistence | grep aof_enabled
docker compose restart backend                              # only if the circuit stays open
```

**Expected impact:** elevated provider latency and possible rate-limit pressure
for a few minutes while the cache warms. That is the entire user-visible
consequence, and it is why AOF persistence exists — not for durability.

**If the recovery fails.** The application degrades to its in-process fallback
cache and keeps serving; cross-process realtime stops. That is a SEV-2 you can
work in daylight, not a SEV-1.

**Verification.** `./scripts/dr/dr_verify.sh --level full` (layer 3 checks Redis
reachability specifically so the "silently running on the degraded fallback"
state cannot pass unnoticed).

---

### R4 — MongoDB corruption, bad migration, or accidental data destruction

**SEV-1. This is the runbook that accepts data loss. Escalate before you run
it (§5.2).**

**Symptom.** Missing or wrong documents, `dropDatabase`/bad migration, mongod
refusing to start with a storage-engine error, or `dr_verify` layer 3 reporting
no collections.

**Diagnosis — do this before deciding, and do not skip it.**

```bash
# Is mongod alive at all?
docker compose ps mongo
docker compose logs --tail 100 mongo

# What is actually in there right now?
./scripts/dr/dr_verify.sh --level full

# What is the newest RESTORABLE artifact? (verifies the file, writes nothing)
./scripts/backup/verify_backup.sh --latest --level structural
```

**Decide the blast radius first**, because it changes the procedure:

| Scope | Procedure |
|---|---|
| One collection | Restore to a **scratch** database and copy that one collection across. The rest of the data stays live and current. |
| Whole database | Full restore with `--drop`. Accepts up to 24 h of loss on everything. |
| Unsure | Restore to a scratch database and *look*. Ten minutes here is cheaper than an unnecessary full restore. |

**Recovery — the whole-database case.**

```bash
# 1. Stop writers. Leave mongo up — the restore needs it.
docker compose stop backend

# 2. Back up the CURRENT (broken) state. You are about to overwrite the evidence.
./scripts/backup/backup_mongo.sh --tier daily --no-prune

# 3. Choose consciously and verify before writing.
./scripts/backup/verify_backup.sh --latest --level structural

# 4. Restore.
./scripts/backup/restore_mongo.sh --latest --drop

# 5. Restart and flush the cache — it describes the database you just replaced.
docker compose start backend
docker compose exec redis redis-cli FLUSHALL
```

**Recovery — the single-collection case.**

```bash
./scripts/backup/restore_mongo.sh --latest --target-db alpha_stock_rescue --yes
docker compose exec -T mongo mongosh "$MONGO_URI" --quiet --eval '
  const src = db.getSiblingDB("alpha_stock_rescue").getCollection("<collection>");
  const dst = db.getSiblingDB("alpha_stock").getCollection("<collection>");
  print("source=" + src.countDocuments({}) + " target=" + dst.countDocuments({}));'
# …inspect, then copy the documents you actually want, then:
docker compose exec -T mongo mongosh "$MONGO_URI" --quiet --eval '
  db.getSiblingDB("alpha_stock_rescue").dropDatabase()'
```

**If the recovery fails.** A corrupt artifact is caught *before* anything is
written (verify-before-write), so nothing was lost by trying. Step back one
artifact — each tier is an independent restore point, and there are 17 of them —
and retry. If every artifact fails on decryption, the passphrase is wrong: stop,
and go to the escrow (§4.3 A2). Do not "try a few more".

**Verification.**

```bash
./scripts/dr/dr_verify.sh --level full \
  --expect-manifest "$(ls -t "$BACKUP_ROOT"/mongo/*/*.manifest.json | head -1)"
```

The `--expect-manifest` comparison is the one that matters: **`mongorestore`
exits 0 on a restore that moved nothing**, and comparing per-collection counts
against the baseline captured at dump time is what turns "the command
succeeded" into "the data is there".

---

### R5 — The rollback also failed / neither version is healthy

**SEV-1. This is an incident, not a deployment problem.**

**Symptom.** `deploy_rollback.sh` reverted and reported that neither version
verified, or the stack will not come up on any tag.

**Diagnosis.** The failure is almost never the application. Work outward:

```bash
./scripts/dr/dr_verify.sh --level full     # which LAYER — 1, 2, 3 or 4?
docker compose config --quiet              # does the compose file still interpolate?
df -h; docker system df                    # a full disk breaks everything, subtly
docker compose logs --tail 200
```

If layer 1 fails, this is a **host** problem (R6/R7). If layer 3 fails, it is a
**data** problem (R4). Two application versions failing identically means the
thing they have in common broke — configuration, a dependency, the disk — not
the code.

**Recovery.** In order, stopping as soon as verification passes:

```bash
# 1. Disk. It is the disk more often than anything else on this list.
docker system prune -f            # NEVER add --volumes: that deletes mongo_data

# 2. Configuration drift. Restore known-good config (R8) if .env was touched.

# 3. A genuinely clean recreate — recreates containers, KEEPS volumes.
docker compose down && docker compose up -d

# 4. If the host itself is suspect → R6/R7.
```

**⚠ `docker compose down -v` is the one command in this repository that
destroys data.** It removes `mongo_data`, `redis_data` and `backend_logs`.
There is no runbook in which it is the right response to an outage.

**Verification.** `./scripts/dr/dr_verify.sh --level full`, then escalate to the
engineering lead regardless of outcome — a double failure means the deployment
pipeline's assumptions are wrong and that outlives the incident.

---

### R6 — Volume or storage failure (host intact)

**Symptom.** mongod will not start with a storage error; a volume is missing or
read-only; the disk is full.

**Diagnosis.**

```bash
df -h                                     # full disk masquerades as corruption
docker volume ls | grep stockassist
docker compose logs mongo --tail 100
```

**Recovery — disk full.** Reclaim, then recreate. Backups are on a *different
filesystem* (BACKUP_AND_RESTORE.md §4), so cleaning up here does not touch them.

```bash
docker system prune -f                    # images/containers/networks, NOT volumes
docker compose restart mongo
```

**Recovery — `mongo_data` lost or unrecoverable.** Redis and uploads volumes are
independent; only the lost one is recreated.

```bash
docker compose stop backend mongo
docker volume rm stockassist_mongo_data   # only after deciding the data is gone
docker compose up -d mongo                # recreated, empty
./scripts/backup/restore_mongo.sh --latest --yes   # empty target needs no confirmation
docker compose start backend
docker compose exec redis redis-cli FLUSHALL
```

Restoring into an *empty* database is the disaster-recovery case, and the
restore script deliberately does not demand confirmation for it — there is
nothing to lose.

**Recovery — `redis_data` lost.** → R3. Nothing to restore.

**If the recovery fails.** If the underlying storage is failing rather than
full, stop repairing in place: the host is untrustworthy → **R7**, and open a
provider ticket in parallel.

**Verification.** `dr_verify.sh --level full --expect-manifest <manifest>`

---

### R7 — Complete server loss

**SEV-1. The scenario the entire backup architecture exists for.**

**Symptom.** The host is unreachable, destroyed, or compromised beyond trust.

**Prerequisites — confirm these before starting, in this order.** If A1 or A2
is not true, recovery is not possible and the next hour is better spent on
communication than on the terminal.

1. Off-host backup artifacts are reachable (A1).
2. The passphrase is retrievable from escrow (A2) — **retrieve it now**.
3. Operator credentials (`MONGO_ROOT_*`) are available (A7).
4. The git remote is reachable (A3).

**Recovery.**

```bash
# 1. New host: Docker Engine + Compose plugin. Nothing else is required.

# 2. Fetch the artifacts. This is the step whose duration is bandwidth-bound
#    and the one most likely to surprise you — see §12 L1.
mkdir -p /srv/backups/stockassist
rclone sync remote:stockassist /srv/backups/stockassist   # or aws s3 sync / restic

# 3. The config manifest is PLAINTEXT — read the commit the secrets belong to,
#    so code and configuration are recovered at the same revision.
grep git_commit /srv/backups/stockassist/config/config-*.manifest.json | tail -1

# 4. Code at that exact revision.
git clone <repo> /srv/stockassist && cd /srv/stockassist && git checkout <commit>

# 5. Secrets over it. Archive paths are repository-relative — no path surgery.
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha512 \
  -pass file:/path/to/escrowed.key \
  -in /srv/backups/stockassist/config/config-<ts>.tar.gz.enc | tar -xzf -
chmod 700 secrets && chmod 600 secrets/* .env

# 6. Data tier first, application second. The backend fails its boot validation
#    against a database that is not there — correctly, and confusingly.
export BACKUP_ROOT=/srv/backups/stockassist
docker compose up -d mongo redis
./scripts/backup/verify_backup.sh --latest --level structural
./scripts/backup/restore_mongo.sh --latest --yes

# 7. The image. There is no registry yet (PH2.7b), so it is rebuilt here.
docker build -t stockassist-backend:<tag> ./backend
docker compose up -d backend

# 8. Prove it.
./scripts/dr/dr_verify.sh --level full \
  --expect-manifest "$(ls -t $BACKUP_ROOT/mongo/*/*.manifest.json | head -1)"
./scripts/dr/deploy_rollback.sh record --note "recovered onto new host after total loss"
```

**Then, before declaring it over:** re-point DNS/proxy, re-run the backup
schedule (§9) on the new host, and confirm the *first* backup on the new host
succeeds. A recovered system with no working backup is one incident away from
being an unrecoverable one.

**If the recovery fails.** The most common blockers, in order: the artifact
cannot be decrypted (passphrase — A2, unrecoverable if truly lost); the artifact
is not there (A1 — this is the risk in §12 L1); the image will not build
(pin the base image and check the network). None of these are fixable during the
incident, which is exactly why they are drilled (§9).

---

### R8 — Configuration corruption or accidental secret rotation

**Symptom.** The backend will not boot and the logs name a configuration
failure; or everyone was logged out at once; or broker links stopped decrypting.

**Diagnosis — which secret changed decides everything:**

| Symptom | Almost certainly | Reversible? |
|---|---|---|
| Boot fails naming a missing/invalid variable | fail-closed validation, working as designed | Yes — fix the value |
| Every user logged out simultaneously | `JWT_SECRET` changed | Yes — restore the old value, or accept the logout |
| Broker tokens fail to decrypt | **`BROKER_TOKEN_KEY` changed** | **NO — see below** |
| Backend cannot authenticate to Mongo | `MONGO_APP_PASSWORD` drifted from the database | Yes — resync both sides |
| Compose interpolation fails | `.env` truncated or corrupted | Yes — restore from the config archive |

```bash
docker compose logs --tail 50 backend | grep -i "config\|secret\|missing"
docker compose config --quiet
./scripts/backup/backup_config.sh --list      # what a restore would bring back
```

**Recovery.**

```bash
# Restore the secret material at the revision it was captured with (§7/R7 step 5).
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha512 \
  -pass file:/path/to/escrowed.key -in <config-artifact> | tar -xzf -
chmod 700 secrets && chmod 600 secrets/* .env

docker compose up -d --no-deps --force-recreate backend
```

**⚠ `BROKER_TOKEN_KEY` is not a credential — it is a decryption key.** Rotating
it does not invalidate sessions; it makes every broker token already stored in
the database **permanently undecryptable**. If the old value cannot be
recovered from the config archive or escrow, the only path forward is to clear
the stored tokens and have every user re-link their broker. Say so plainly and
early; there is no technical recovery.

**If the recovery fails.** If the config archive predates the last legitimate
rotation, you are restoring a secret the running system has already moved past.
Resync the *other* side instead (rotate the Mongo user's password to match, for
example) rather than forcing the archive's value everywhere.

**Verification.** `./scripts/dr/dr_verify.sh --level full`, then confirm a real
login works — boot validation passing does not prove `JWT_SECRET` is the one
users' sessions were signed with.

---

### R9 — Suspected compromise

**SEV-1. Recovery rules change. Read this before running any other runbook.**

If the host may be compromised, **restoring onto it is not recovery** — it
reinstates the data next to whatever got in. Likewise the local backup
directory must be assumed compromised: ransomware that reaches the application
host reaches `BACKUP_ROOT` too.

1. **Escalate first** (§5.2). Do not clean up. Do not restart. Evidence has a
   shorter half-life than the outage.
2. Isolate the host (network, not power — memory is evidence).
3. Recover onto **clean infrastructure** from the **off-host, object-locked**
   copy: R7, with a new host and artifacts that never touched the old one.
4. **Rotate every secret** on the new deployment (`.claude/SECRETS.md`), and
   treat the audit trail in `security_audit_logs` as the primary source for
   what was done and when.
5. `BROKER_TOKEN_KEY` rotation forces broker re-linking (R8). Plan the user
   communication before you rotate, not after.

Object lock / write-once on the backup destination is the control that makes
step 3 possible. Without it, this runbook has no source to recover from.

---

### R10 — Backup job failing (no outage yet)

**SEV-3 today, SEV-1 in three weeks.** A silent backup failure is a disaster
that has already happened and has not been noticed. It is a runbook because it
is the only entry here that is *cheap* to fix and *catastrophic* to ignore.

**Symptom.** A non-zero exit in the cron mail, a `*.rejected` artifact, or a
retention count that has stopped growing.

**Diagnosis.**

```bash
ls -lt "$BACKUP_ROOT"/mongo/daily | head            # is anything recent?
ls "$BACKUP_ROOT"/mongo/*/*.rejected 2>/dev/null    # quarantined evidence
./scripts/backup/verify_backup.sh --all --level checksum
```

Then read BACKUP_AND_RESTORE.md §17 — the failure messages are documented
against their causes there.

**Recovery.** Fix the cause, take a backup by hand, and **drill it** — a backup
taken to close an alert that has never been restored from is exactly the file
this whole architecture exists to avoid:

```bash
./scripts/backup/backup_mongo.sh --tier daily
./scripts/backup/verify_backup.sh --latest --level drill
```

**Verification.** `drill OK`, and the next scheduled run succeeds unattended.

---

## 8. Deployment rollback

### 8.1 The four facts

"Roll back the deployment" is one sentence and four separate facts. A
deployment that cannot answer all four cannot be rolled back:

1. Which version is running right now?
2. Which version was running before it?
3. **Is that previous image still on this host?**
4. Did the rollback actually take effect?

There is no CD pipeline and no image registry yet (PH2.7b), so nothing answers
1 and 2 on its own: the tag lives in a hand-edited `.env`, and `docker compose
up -d` with an unchanged tag is a **silent no-op**. `deploy_rollback.sh` closes
all four — an append-only ledger for 1–2, a hard precondition for 3, and a
verified apply plus `--expect-version` for 4.

```bash
./scripts/dr/deploy_rollback.sh record --note "release 1.4.0"   # after every deploy
./scripts/dr/deploy_rollback.sh current
./scripts/dr/deploy_rollback.sh list
./scripts/dr/deploy_rollback.sh rollback --previous
./scripts/dr/deploy_rollback.sh rollback --to 1.3.0 --dry-run
```

**The ledger lives under `$BACKUP_ROOT`** (`deployments.tsv`) because the
question "what were we running before the incident?" is asked most urgently in
the incident where the host is gone — and `$BACKUP_ROOT` is the one directory
already copied off-host. It records timestamps, tags, commits, git cleanliness,
actor and note. **Never secrets:** it is synced to object storage.

### 8.2 What the rollback refuses to do

| Guard | Why |
|---|---|
| Refuses if the target image is **not on this host** | Without a registry, a pruned image is not recoverable. Discovering that *after* recreating the backend converts a rollback into an outage. Nothing is touched. |
| Requires the tag to be **typed**, after an explicit migration warning | A `y/N` at 03:00 is answered by muscle memory. The one question a script cannot answer is asked out loud. |
| Records the roll-**forward** point *before* changing anything | Otherwise a rollback is a one-way door. |
| Writes `.env` **atomically** (temp + rename, mode 600) | A half-written `.env` breaks compose interpolation entirely — worse than either version. |
| Recreates **only the backend** (`--no-deps`) | Restarting mongo and redis for an application rollback is a cold start on every tier at once. |
| **Automatically reverts** if the target does not become healthy | A rollback to a version that is *also* broken is the worst of the three outcomes: it burns the operator's remaining confidence in the mechanism. Back to a known state, then diagnose. |
| Does **not** `git checkout` | Moving an operator's working tree during an incident, mid-diagnosis, is clever exactly once. The commit is recorded and the command printed; a human runs it. |
| Does **not** reverse data migrations | It cannot. See R1's warning. |

### 8.3 Configuration rollback

Configuration has two halves and they roll back differently:

| Half | Where | Rollback |
|---|---|---|
| Tracked config — `docker-compose*.yml`, Dockerfiles, `redis.conf` | git | `git checkout <commit> -- <path>` then `docker compose up -d`. Reviewed, diffable, exact. |
| Secret material — `secrets/`, `.env` | the encrypted config archive | R8. |

This split is deliberate and is why `backup_config.sh` excludes tracked files:
`git clone` restores them at the exact reviewed revision, while a backup copy
restores them at whatever they happened to be on the host. The config manifest
records the **git commit** precisely so the two halves can be reunited at the
same point in time.

---

## 9. Verification and drills

### 9.1 The verification tool

```bash
./scripts/dr/dr_verify.sh --level quick        # layers 1, 2, 4 — no database work
./scripts/dr/dr_verify.sh --level full         # all four layers
./scripts/dr/dr_verify.sh --level full --expect-manifest <manifest.json>
./scripts/dr/dr_verify.sh --level quick --expect-version 1.3.0
```

| Layer | Checks | The failure it catches |
|---|---|---|
| **1 Host** | docker daemon, compose file interpolates | A recovered host whose `.env` never arrived — the most common post-rebuild failure. |
| **2 Containers** | created, running, healthy, **restart count** | "It is up now" about a container that is crash-looping. |
| **3 Data** | Mongo reachable, **has collections**, counts match the manifest, Redis reachable | **A recovered stack serving an empty database.** Passes every other layer. |
| **4 Application** | live / ready / startup separately, **which build is running** | A rollback that silently did not take effect. |

Three design decisions worth knowing:

* **Every check runs; dependent checks report SKIP, not FAIL.** A test suite
  should stop early; a diagnostic must not. "Containers up, Mongo fine, Redis
  unreachable" is a different incident from "nothing is running", and getting
  that shape one round-trip at a time is how a fifteen-minute recovery becomes
  an hour. A cascade of failures pointing at four layers sends the operator to
  the wrong one.
* **An empty database is a FAILURE, not a warning.** It is the most expensive
  thing this script can catch and it is invisible everywhere else.
* **It is safe to run against a broken system.** It is the diagnosis tool in §6
  and the verification tool here — deliberately the same tool, so the command
  that told you what broke is the command that tells you it is fixed.

Exit codes: `0` all passed · `1` a check failed · `2` usage error. The
machine-readable summary is one line on stdout; the human report is on stderr.

### 9.2 The post-recovery checklist

Run in order. Do not close the incident with an unchecked box; write "N/A —
reason" instead.

- [ ] `dr_verify.sh --level full` exits 0
- [ ] Collection counts match the manifest baseline (`--expect-manifest`)
- [ ] The running build is the intended one (`--expect-version`)
- [ ] A real login works end to end (proves `JWT_SECRET`, not just boot validation)
- [ ] One portfolio and one journal entry render with plausible, current data
- [ ] Realtime works: a live quote updates in the UI (proves Pub/Sub, not just PING)
- [ ] Redis cache warmed — provider latency back to normal
- [ ] `docker compose ps` shows zero restarts since recovery
- [ ] **The backup schedule runs on this host and the next backup succeeds**
- [ ] The deployment is recorded: `deploy_rollback.sh record --note "<incident>"`
- [ ] Data-loss window quantified and communicated to users if non-zero
- [ ] Postmortem opened from `docs/runbooks/POSTMORTEM_TEMPLATE.md`

### 9.3 Drills

**An untested recovery plan is a document, not a capability.** Each drill below
exists to test one assumption from §4.3, on a cadence proportional to how
expensive its failure is.

| Cadence | Drill | Assumption tested | Time |
|---|---|---|---|
| Every backup | Automatic structural verification | the artifact is intact and decryptable | 0.31 s |
| Daily | `verify_backup.sh --all --level checksum` | nothing has rotted | ~0.1 s/artifact |
| **Monthly** | `verify_backup.sh --latest --level drill` | **a real restore works** | ~5 s |
| **Monthly** | `dr_verify.sh --level full` on the live stack | the verifier still matches reality | ~1 s |
| **Monthly** | Redis no-TTL tripwire (BACKUP_AND_RESTORE.md §6.2) | Redis is still disposable | minutes |
| **Quarterly** | **Off-host fetch drill** — pull the newest artifact from remote storage *to a different machine* and restore it there | **A1 — the assumption this plan most depends on and least verifies** | ~1 h |
| **Quarterly** | **Escrow drill** — a second person retrieves the passphrase and decrypts an artifact **without asking the first person** | A2, A5 | ~30 min |
| **Quarterly** | Rollback drill on staging: deploy, `rollback --previous`, verify | A6, and the ledger's accuracy | ~15 min |
| **Annually** | **Full R7 rehearsal** onto a fresh host, timed end to end | the RTO in §4 | half a day |

Two rules about drills, both learned expensively by other people:

1. **A drill that is not timed does not test the RTO.** Write down the wall
   clock at the start and the end. The number in §4 is only as good as the last
   drill that produced it.
2. **The escrow drill must be run by someone who did not set it up.** The
   failure mode it tests is "only one person could actually get to it", and the
   person who created it cannot detect that.

---

## 10. Postmortems

Every SEV-1 and SEV-2 gets a written postmortem within **five working days**,
using `docs/runbooks/POSTMORTEM_TEMPLATE.md`. It is blameless by construction:
the output is a list of *system* changes, and "be more careful" is never one of
them.

The reason it is in this document rather than a process wiki: the runbooks above
are only correct until the system changes, and the postmortem is the mechanism
that keeps them correct. **A postmortem that does not result in an edit to a
runbook, a new check in `dr_verify.sh`, or a new drill has not finished.**

---

## 11. Measured results

Executed **2026-08-04** on the PH2.10 development host (Apple Silicon, MongoDB
8.0.13, mongo tools 100.14.0, `BACKUP_MODE=direct`, AES-256 on) against the real
`alpha_stock_db`.

| Operation | Result |
|---|---|
| Full backup of the live database (dump → gzip → encrypt → checksum → publish → structural verify) | **1.63 s**, 87.9 KB artifact |
| **Restore into a scratch database, 21 collections** | **4.48 s** wall, 2 s in `mongorestore`, **21/21 collections matched** the manifest baseline |
| `dr_verify.sh --level full` (4 layers, incl. manifest comparison) | **1.10 s** |
| Config archive of all secret material (14 files) | **0.72 s**, 10.0 KB |
| **Config recovery** — decrypt + unpack into a clean tree | **0.17 s**, 14/14 files, git commit recorded in the manifest |
| Compose interpolation of a rolled-back tag (`BACKEND_IMAGE_TAG=1.3.0` → `image: stockassist-backend:1.3.0`) | **verified** without a Docker daemon |

**Fidelity checks — queried, not inferred:**

* A single extra document inserted into one collection was **detected** by
  `--expect-manifest` (`MISMATCH admin_audit_logs expected=7 actual=8`) and the
  check returned to PASS after the document was removed. The comparison is not
  vacuous.
* An empty database is reported as a **failure**, not a pass.
* A rollback whose target image is absent left `.env` byte-identical and issued
  no `docker compose up` (asserted in the test suite; reproduced live with the
  daemon down).
* `--previous` skipped two repeats of the running tag and selected the last
  genuinely different version.

Scale figures for the data tier (205 000 documents / 26.3 MB, and the projection
to 1 GB and 10 GB) are in BACKUP_AND_RESTORE.md §8 and §8.1 and are not repeated
here.

**41 hermetic tests** (`backend/tests/test_disaster_recovery.py`) assert the
safety properties of both scripts with `docker`, `curl` and `mongosh` stubbed;
the PH2.9 suite (39) still passes; flake8 clean.

---

## 12. Known limitations

**L1 — The off-host copy is still documented, not implemented.** This is the
largest risk in this plan and it is not a PH2.10 regression — it is
BACKUP_AND_RESTORE.md L2 restated where it hurts most. **R7 (complete server
loss) is unexecutable without it.** Every other limitation here is a degradation;
this one is a total-loss scenario. Fix: add the `rclone`/`aws s3 sync` line to
the backup cron entry, enable object lock, and run the quarterly fetch drill.

**L2 — Runbooks R1–R3, R5–R7 are unexecuted end to end.** No Docker daemon was
available in the PH2.10 sprint environment (the same constraint as PH2.7 L1 and
PH2.9 L6). The data-tier and configuration paths were executed for real against
a live MongoDB; the container-level steps are verified only by construction
(compose interpolation validated, commands syntax-checked) and by the stubbed
test suite. **Run §9.3's monthly drills once on a real stack before treating
this plan as proven.**

**L3 — Detection is manual.** Every RTO in §4.2 begins at "someone notices".
Alerting, error tracking and the uptime check are roadmap PH2.10 and are the
single highest-leverage improvement available to the RTO — worth more than any
change to these scripts.

**L4 — RPO is 24 h and bounded by backup frequency, not by seconds.** No
point-in-time recovery; no cross-collection consistency guarantee (§4.4). Fix:
single-node replica set + `mongodump --oplog` (§14).

**L5 — No registry, so rollback depends on the image still being on the host.**
`docker image prune` between a deploy and its rollback removes the ability to
roll back. The script fails loudly and early rather than half-way, and prints
the rebuild command, but it cannot conjure the image. Closing this is PH2.7b.

**L6 — The deployment ledger records only what goes through the script.** A
deploy applied by editing `.env` by hand is invisible to it, and `--previous`
will then propose a stale tag. The script reads the *running container* rather
than the env file for "current" specifically to limit the damage, but the ledger
is only as complete as the habit. Automating it is PH2.7b.

**L7 — Single-tenant, single-host, no failover.** Every recovery here is an
outage. There is no degraded-but-serving mode for a MongoDB or host failure.

**L8 — Uploads have no drill.** The volume is declared but not mounted
(BACKUP_AND_RESTORE.md §7). When uploads ship, R6 and R7 both need an upload
restore step and this document must be updated in the same change.

**L9 — No dependency-outage runbook.** Anthropic, Gemini, the market data
providers and the brokers can all fail while this deployment is perfectly
healthy. That is a *degradation* handled by the Market Gateway's failover and
the Redis circuit breaker, not a disaster recovery scenario — but it is the
most likely thing to page someone, and it belongs in an incident-response
document rather than here.

---

## 13. Prerequisites — the pre-disaster checklist

Verify quarterly. Every unchecked line is a runbook above that will fail at the
moment it is needed.

- [ ] `BACKUP_ROOT` is on a **different filesystem** from the Docker volumes
- [ ] The off-host sync runs, and has been **fetched from** at least once (A1)
- [ ] Object lock / write-once is enabled on the backup destination (R9)
- [ ] The passphrase is in an offline escrow reachable by **≥ 2 people** (A2)
- [ ] Operator credentials (`MONGO_ROOT_*`) are retrievable independently (A7)
- [ ] The git remote holds the deployed revision (A3)
- [ ] `deploy_rollback.sh record` runs after every deployment (A6, L6)
- [ ] The previous image is retained on the host (no aggressive `image prune`)
- [ ] Cron includes backup, verification **and the monthly drill** (§9.3)
- [ ] At least two people know this document exists and where the escrow is (A5)
- [ ] A monthly `dr_verify.sh --level full` has been run against the live stack

---

## 14. Future improvements, in the order they are worth doing

Ordered by risk removed per unit of work — not by sophistication.

1. **Wire up the off-host copy and drill a fetch.** One cron line. Removes L1,
   the only limitation here that makes a whole runbook unexecutable.
2. **Alerting + uptime check** (roadmap PH2.10). Cuts 30 minutes off every RTO
   in §4.2 and closes BACKUP_AND_RESTORE.md L4 (silent backup failure).
3. **A registry + CD** (PH2.7b). Makes rollback independent of what happens to
   survive on the host (L5) and records deployments automatically (L6).
4. **Single-node replica set + `--oplog`.** Point-in-time recovery: RPO 24 h →
   minutes, and cross-collection consistency. A compose and connection-string
   change with one restart.
5. **A staging environment that DR drills run against.** Turns the quarterly
   rehearsal from an event into a routine.
6. **Managed MongoDB (Atlas) or a replica set with a secondary.** The first step
   that converts a *recovery* into a *failover* — minutes to seconds — and the
   first that costs real money. Do the four above first; they remove more risk
   for less.
7. **Multi-host / multi-region.** Only once the business case demands an SLA
   that a single host cannot meet. Everything above is cheaper.

---

## 15. See also

* [`BACKUP_AND_RESTORE.md`](BACKUP_AND_RESTORE.md) — what is backed up, encryption, retention, verification levels, the restore procedure, secret recovery
* [`MONITORING.md`](MONITORING.md) — the three health probes, metrics, request correlation, troubleshooting
* [`LOGGING.md`](LOGGING.md) — log streams, retention, and where the evidence lives
* [`docs/runbooks/POSTMORTEM_TEMPLATE.md`](../runbooks/POSTMORTEM_TEMPLATE.md)
* [`docs/infrastructure/REDIS.md`](../infrastructure/REDIS.md) · [`docs/deployment/DOCKER_COMPOSE.md`](../deployment/DOCKER_COMPOSE.md) · [`docs/deployment/SECRETS.md`](../deployment/SECRETS.md)
* `.claude/PRODUCTION_HARDENING.md` §11 — recovery strategy and the RPO/RTO commitments

---

## 16. Document history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-08-05 | Created by PH2.10 — Disaster Recovery & Business Continuity. |

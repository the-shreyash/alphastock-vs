# Backup & Restore

Authoritative document for how StockAssist AI's persistent data is backed up,
how it is restored, and how both are proved to work. Delivered by **PH2.9**.

> **The only thing that distinguishes a backup from a file is a restore.**
> Everything in this document is arranged around that sentence. A backup job
> that has run every night for a year and has never been restored from is not a
> backup strategy — it is a disk-usage strategy.

---

## 1. What this gives you, and what it deliberately does not

**It gives you**

* Automated, encrypted, self-describing MongoDB backups on a grandfather-father-son
  rotation, with per-collection document counts recorded at dump time.
* Three graduated levels of verification — checksum, structural, and a real
  restore drill into a scratch database — so verification can run nightly at a
  cost that is actually affordable.
* A restore path that verifies before it writes, refuses to overwrite a
  populated database unattended, and checks afterwards that the data landed.
* An encrypted backup of the secret material without which a restored database
  is useless.
* An upload-storage backup path that is ready before uploads ship.
* A recorded, measured restore drill — with real numbers, in §8.

**It deliberately does not give you**

* **Off-host storage.** These scripts write to a local `BACKUP_ROOT`. Getting
  those bytes to another machine is one `rclone`/`aws s3 sync`/`restic` line in
  the same cron entry, and *it is not optional* — see §4.
* **Point-in-time recovery.** Requires converting mongod to a replica set. §11.
* **Cloud-managed backup** (AWS Backup, Atlas continuous backup). Out of scope
  for PH2.9 by design; §11 records the migration path.
* **Alerting on backup failure.** The scripts exit non-zero and say why; wiring
  that to a monitored channel is PH2.10's minimum alert set.

---

## 2. Architecture

```
                      ┌──────────────────────────┐
                      │   StockAssist backend    │
                      └────────────┬─────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
   ┌────▼─────┐              ┌─────▼─────┐            ┌───────▼────────┐
   │ MongoDB  │              │   Redis   │            │ Upload storage │
   │ mongo_   │              │ redis_    │            │ backend_       │
   │ data vol │              │ data vol  │            │ uploads vol    │
   └────┬─────┘              └─────┬─────┘            └───────┬────────┘
        │                          │                          │
  SYSTEM OF RECORD          DISPOSABLE CACHE            USER CONTENT
  must be backed up      NOT backed up — §6             §7
        │                          │                          │
        │ mongodump --archive      │ (AOF is a warm-start     │ tar
        │ --gzip                   │  optimisation, not a     │
        │                          │  backup — see §6)        │
        ▼                          ▼                          ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │  scripts/backup/  →  stream → gzip → AES-256 → <name>.partial     │
  │                      → sha256 → atomic rename → manifest.json     │
  └────────────────────────────────┬──────────────────────────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  $BACKUP_ROOT                │
                    │    mongo/{daily,weekly,      │
                    │           monthly}/          │
                    │    config/                   │
                    │    uploads/                  │
                    └──────────────┬───────────────┘
                                   │
                     ┌─────────────▼─────────────┐
                     │  OFF-HOST COPY  (§4)      │  ← you must add this
                     │  object storage / another │
                     │  machine / offline media  │
                     └─────────────┬─────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  verify_backup.sh            │
                    │    checksum → structural     │
                    │              → drill         │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  restore_mongo.sh  (§9)      │
                    └──────────────────────────────┘
```

### The files

| File | Role |
|---|---|
| `scripts/backup/lib.sh` | Shared library: config, encryption, checksums, manifests, retention, transport, guards. Sourced, never executed. |
| `scripts/backup/backup_mongo.sh` | Full MongoDB backup → manifest → retention → self-verify. |
| `scripts/backup/restore_mongo.sh` | Restore, with verify-before-write and verify-after-write. |
| `scripts/backup/verify_backup.sh` | Three verification levels, including the drill. |
| `scripts/backup/backup_config.sh` | Encrypted archive of secret material. Encryption mandatory. |
| `scripts/backup/backup_uploads.sh` | Upload-volume tarball and restore. |
| `backend/tests/test_backup_restore.py` | 39 hermetic tests asserting the properties above. |

---

## 3. Prerequisites

On the host that runs the backups:

| Tool | Needed for | Notes |
|---|---|---|
| `bash` ≥ 3.2 | everything | macOS's 3.2 is supported; no bash-4 syntax is used. |
| `openssl` | encryption | Present on every Linux distro and macOS. |
| `gzip` | integrity checking | `gzip -t` is the structural verifier. |
| `docker` | `BACKUP_MODE=docker` | Default mode. |
| `mongodump`, `mongorestore`, `mongosh` | `BACKUP_MODE=direct` | Not needed in `docker` mode — they run inside the mongo container. |

---

## 4. Storage — read this before scheduling anything

`BACKUP_ROOT` defaults to `<repo>/backups`, which is git-ignored. **That default
is for a developer laptop, not for production.**

Two rules, both of which have their own well-known failure story:

1. **A production `BACKUP_ROOT` must be on a different filesystem from the
   Docker volumes.** A backup that shares a disk with the database it protects
   survives exactly the failures that do not matter — a bad migration, a
   `dropDatabase` — and none of the ones that do. A dead disk takes both.

2. **The backups must be copied off the host.** These scripts produce encrypted,
   checksummed, self-describing files precisely so that shipping them elsewhere
   is a one-line problem. Add it to the same cron entry:

   ```bash
   /srv/stockassist/scripts/backup/backup_mongo.sh && \
     rclone sync "$BACKUP_ROOT" remote:stockassist-backups --immutable
   ```

   Enable **object lock / write-once** on the destination bucket if it supports
   it. Ransomware that reaches the application host reaches its backup directory
   too; object lock is the difference between an incident and a closure.

Sizing: the artifacts compress well (measured **13.2 : 1** in §8). At the default
retention — 7 daily + 4 weekly + 6 monthly = **17 artifacts** — budget roughly
17 × the compressed size of one full backup, plus headroom.

---

## 5. MongoDB

### 5.1 What it holds, and why it is the only tier that must be backed up

Users, portfolios, holdings, trades, the trade journal, sessions, notifications,
support tickets, feature flags, and the security audit trail. None of it is
reconstructible from anywhere else. Market data is refetchable; this is not.

### 5.2 How the backup is taken

`mongodump --archive --gzip` streams to stdout; the stream is encrypted and
lands on disk already compressed and encrypted. **At no point does a plaintext
copy of the database exist on a filesystem**, so there is nothing to forget to
delete and nothing for an interrupted run to leave behind.

The four publication properties, all enforced in `bk_publish_artifact`:

| Property | The failure it prevents |
|---|---|
| Write to `<name>.partial`, rename only after checksumming | A truncated file that looks complete, chosen during an incident |
| Refuse an empty result | The "all our backups were 0 bytes" incident |
| Checksum, rename, **checksum again** | `mv` across a filesystem is a copy; a copy is where a full disk truncates silently |
| `umask 077` around the whole thing | An artifact briefly world-readable |

### 5.3 Consistency — the caveat that must not be skipped

Against the **standalone** mongod that `docker-compose.yml` runs today,
`mongodump` is consistent *within* each collection but **not across
collections**: a write landing after `trades` is dumped and before `portfolios`
is dumped appears in one and not the other.

For crash recovery this is acceptable — you are recovering to "approximately
03:00", not to a transaction boundary. **It is not sufficient for a financial
reconciliation that must tie across collections.** Removing the caveat requires
a single-node replica set, which enables `mongodump --oplog` and point-in-time
restore. See §11.

### 5.4 The manifest

Every artifact has a sibling `*.manifest.json`:

```json
{
  "schema": 1,
  "kind": "mongo",
  "database": "alpha_stock",
  "tier": "daily",
  "created_at": "2026-08-04T19:00:36Z",
  "artifact": "mongo-alpha_stock-20260804T190034Z-daily.archive.gz.enc",
  "format": "mongodump-archive-gzip",
  "encryption": "openssl-aes-256-cbc-pbkdf2-600000",
  "sha256": "ed7407a0…",
  "size_bytes": 2084400,
  "duration_seconds": 1,
  "backup_mode": "direct",
  "consistency": "per-collection (standalone mongod, no --oplog)",
  "collections": {"trades": 200000, "users": 5000}
}
```

`collections` is the load-bearing field. `mongorestore` exits 0 on a restore that
moved nothing; comparing restored counts against a baseline captured at dump
time is what turns *"the command succeeded"* into *"the data is there"*.

### 5.5 Retention

Grandfather-father-son, **count-based per tier**, decided at backup time from the
UTC calendar and baked into the filename and directory:

| Tier | Taken | Default kept | Variable |
|---|---|---|---|
| `daily` | every day that is neither the 1st nor a Sunday | 7 | `BACKUP_RETAIN_DAILY` |
| `weekly` | Sundays | 4 | `BACKUP_RETAIN_WEEKLY` |
| `monthly` | the 1st of the month | 6 | `BACKUP_RETAIN_MONTHLY` |
| `config` | on demand | 20 | `BACKUP_RETAIN_CONFIG` |

**Why count-based, when PH2.6 made log retention age-based first.** Logs carry a
legal commitment phrased in wall-clock time ("audit records kept 365 days"), so
age must win. Backups carry a *recovery* commitment phrased in coverage ("we can
restore to any of the last 7 days"), and coverage is a count. Pruning a daily
backup because it turned 8 days old, in a week when the job failed twice, would
silently reduce coverage to five restore points while every wall-clock rule
still passed. Count-based retention cannot do that.

Five safety rules, each present because its absence has destroyed someone's
backups:

1. **Prune runs only after the new backup exists and has been verified.** Prune
   first, fail to produce the replacement, and a retention policy becomes a
   data-loss policy.
2. **Only files matching the pattern the script itself writes are considered.**
   An operator's `KEEP-before-the-v2-migration.archive.gz.enc` is invisible to
   the pruner.
3. **A keep-count below 1 is rejected**, so an empty `BACKUP_RETAIN_DAILY=`
   cannot evaluate to "keep zero".
4. **The newest artifact in a tier is never deleted**, whatever the arithmetic
   says.
5. **Artifact first, manifest second**, so an interrupted prune leaves an
   orphaned manifest (noise) rather than an unidentifiable artifact (a problem).

### 5.6 Encryption

One path: **`openssl enc -aes-256-cbc -salt -pbkdf2 -iter 600000 -md sha512`**.

The tempting design offers GPG when present and OpenSSL otherwise. It is a trap:
the encrypt path runs nightly and the decrypt path runs during an outage, so a
two-tool scheme means the restore is the first time anyone discovers the
recovery host has only one of the two. **One path that is always available beats
a better path that is sometimes available.**

In `APP_ENV=production`, a missing passphrase is a **hard failure**, not a
downgrade to plaintext. A plaintext dump of the `users` collection is a breach
waiting for whoever finds the backup directory.

> **Honest limitation.** CBC gives confidentiality, not authenticity. Accidental
> corruption is caught (§5.7); a deliberate tamper by someone who can rewrite
> both the artifact and its manifest is not. Object lock on the backup
> destination closes this. The manifest records `encryption` per artifact
> specifically so a future authenticated format can be introduced without
> orphaning today's files.

### 5.7 Verification — three levels, one cost curve

| Level | What it proves | Needs a database? | Cost | Run |
|---|---|---|---|---|
| `checksum` | Manifest present, schema understood, SHA-256 still matches. Catches bit rot, truncation, a partial transfer to object storage. | no | **0.12 s** | on every off-host sync |
| `structural` | The above, **plus** decrypt and run the whole payload through gzip's CRC, then confirm the mongodump archive magic number. Proves the passphrase works, the ciphertext is intact end to end, and the payload really is a dump. | no | **0.31 s** | **every backup, automatically** |
| `drill` | The above, **plus** a real restore into a scratch database, a per-collection count comparison, and cleanup. | yes | **~5 s** at 26 MB | monthly |

Two details that carry more weight than they look:

* **`gzip -t` is the workhorse.** `mongodump --archive --gzip` gzips the entire
  archive stream, so gzip's CRC-32 covers every byte; one flipped bit anywhere
  fails it. In the encrypted case it is also the closest thing this scheme has
  to an authentication tag — AES-CBC will happily "decrypt" under a wrong
  passphrase and emit garbage, and garbage does not pass a CRC.
* **The magic-number check catches what a CRC cannot**: a perfectly valid gzip
  file whose contents are not a mongodump archive. That is what a shell
  redirection which captured an error message looks like.

**A backup that fails its post-write verification is renamed `*.rejected`, not
deleted.** `.rejected` is outside every glob the system uses — `--latest` cannot
select it, retention does not count it, a restore cannot reach it by accident —
so it is inert. But it survives, because the file is the evidence for why the
backup failed.

**Why the drill restores into a scratch database.** The intuitive drill restores
over the real database on staging. That makes the drill itself a risk, so it
gets run rarely, so it stops being run at all. `--nsFrom`/`--nsTo` remapping into
`<db>__drill_<timestamp>` makes the drill non-destructive *by construction* —
and a drill you actually run beats a perfect drill you do not.

---

## 6. Redis — what is recoverable, what is disposable

**Decision: Redis is NOT backed up, and that is correct.**

`docker/redis/redis.conf` (PH2.7) runs AOF with `appendfsync everysec` and RDB
disabled. It is easy to read that as durability. It is not:

| What lives in Redis | Recoverable from | Backup needed? |
|---|---|---|
| Market-data cache entries | The provider APIs, via the Market Gateway | No |
| Pub/Sub realtime traffic | Not stored at all; in flight only | No |
| — | — | — |
| Sessions | **MongoDB** (`backend/security/sessions.py`) | Covered by §5 |
| Rate-limit counters | **MongoDB** | Covered by §5 |
| Audit log | **MongoDB** | Covered by §5 |

Nothing in Redis is a system of record. That is a deliberate architectural
property, not an accident — the durable things were put in MongoDB *precisely
so* Redis could stay disposable.

**So why is persistence enabled at all?** Not for durability — for **restart
behaviour**. Without it, every Redis restart empties the cache and every backend
replica simultaneously re-fetches the entire quote universe from rate-limited
third-party APIs. That self-inflicted thundering herd turns a 2-second restart
into minutes of degraded market data. **The AOF is a warm-start optimisation,
and a warm-start optimisation is not a backup.**

### 6.1 Recovery procedure for Redis

There isn't one, and there does not need to be. If `redis_data` is lost:

```bash
docker compose up -d redis          # starts empty
docker compose exec redis redis-cli INFO persistence   # aof_enabled:1
```

The cache refills on demand. Expect **elevated provider latency and possible
rate-limit pressure for the first few minutes** while it warms — that is the
entire user-visible consequence, and it is why persistence exists.

To reduce even that, restart Redis outside market hours, or stagger backend
replica restarts so they do not all miss simultaneously.

### 6.2 The tripwire

This decision is only valid while the table above is true. **If anything durable
is ever written to Redis, this section becomes wrong and nobody will notice.**

Include this in the monthly checklist (§10). Every key in this deployment should
carry a TTL; a key without one is a candidate for something that was meant to
last:

```bash
docker compose exec redis redis-cli --scan --count 1000 \
  | while read -r k; do
      [ "$(docker compose exec -T redis redis-cli TTL "$k")" = "-1" ] && echo "NO TTL: $k"
    done
```

If that prints anything, either give the key a TTL or move the data to MongoDB.
Mixing evictable and non-evictable data in one Redis is how you discover, during
an incident, which of the two your code assumed — and `maxmemory-policy
allkeys-lru` will evict it without asking.

---

## 7. Upload storage

`docker-compose.yml` **declares** the `backend_uploads` volume but does not mount
it (mounting requires the image to pre-create the directory owned by uid 10001 —
a PH2.1 change). So there is nothing to back up yet, and
`backup_uploads.sh` exits cleanly saying so.

It exists now anyway, deliberately: the moment uploads ship — avatars, imported
broker statements, exported reports — they are live user data on day one, and
the first day of a new data store is exactly when nobody remembers to add it to
the backup rotation.

```bash
./scripts/backup/backup_uploads.sh                         # the Docker volume
./scripts/backup/backup_uploads.sh --path /srv/uploads     # a host directory
./scripts/backup/backup_uploads.sh --restore <artifact> --yes
```

Three decisions worth knowing:

* **A helper container reads the volume.** A named volume lives inside Docker's
  storage area — on macOS and Windows, inside a VM — and host `tar` cannot read
  it. The helper image defaults to the **Redis image the stack already pins and
  has already pulled**, rather than `alpine:latest`: an unpinned `:latest` in a
  backup path is a third party who can change what runs against your data.
* **The volume is mounted `:ro`.** A backup must not be able to modify what it
  is backing up.
* **Restore unpacks *over* the existing tree.** Files in the archive are
  overwritten; files added since the backup are left alone. The alternative
  deletes every file uploaded after the backup was taken, turning a partial loss
  into a total one.

**When uploads ship**, a `0 files` result becomes an **alert, not a pass** — the
script says so in its own output.

---

## 8. Measured results

Executed 2026-08-04 on the PH2.9 development host (Apple Silicon, MongoDB 8.0.13,
`mongodump`/`mongorestore` 100.14.0, `BACKUP_MODE=direct`, AES-256 encryption on).

**Dataset:** 205 000 documents across 2 collections, **26.3 MB** logical data,
one compound secondary index.

| Operation | Result |
|---|---|
| Backup (dump → gzip → encrypt → checksum → publish → structural verify) | **2.06 s** |
| Artifact size | **1.99 MB** (**13.2 : 1** compression) |
| `verify --level checksum` | **0.12 s** |
| `verify --level structural` (decrypt + full CRC + magic) | **0.31 s** |
| `verify --level drill` (restore to scratch + compare + drop) | **~5 s** end to end, 2 s in `mongorestore` |
| Restore into an empty database (`restore_mongo.sh`) | **3.51 s** total, 2 s in `mongorestore` |

**Fidelity check after restore** — not inferred, queried:

```
source trades  = 200000  restored = 200000
source users   =   5000  restored =   5000
source indexes = ["_id_","sym_1_ts_-1"]
restored idx   = ["_id_","sym_1_ts_-1"]
sample doc identical = true
```

Indexes are restored, not skipped: `--noIndexRestore` is deliberately **not**
passed, because index build time is a real and often dominant part of RTO, and a
drill that skips it produces an RTO figure that is wrong in the optimistic
direction.

Also verified in the same session, against the live database:

* An encrypted backup of the real `alpha_stock_db` (21 collections) drilled to
  **21/21 collections matched**, scratch database dropped afterwards, no residue.
* A **wrong passphrase** is detected and reported as a decryption failure, not a
  pass.
* A **corrupted artifact** fails checksum; a corrupted artifact **with a rewritten
  manifest** still fails the gzip CRC.
* A **non-empty target database** refuses an unattended `--drop` restore.

### 8.1 Extrapolating to production

Backup and restore time scale roughly linearly with data size on this shape of
data. At the measured **~13 MB/s** logical throughput:

| Logical data | Backup | Restore | Artifact |
|---|---|---|---|
| 26 MB *(measured)* | 2 s | 3.5 s | 2 MB |
| 1 GB | ~1.5 min | ~2.5 min | ~78 MB |
| 10 GB | ~13 min | ~22 min | ~780 MB |

**Re-measure at your real data size before trusting §12's RTO.** These are
projections from one point, on fast local storage, with no concurrent
application load. Production numbers will be worse, and the drill is how you find
out by how much.

---

## 9. Restore procedure

### 9.1 The ordered checklist

**Stop the application first.** A restore that runs while the backend is writing
produces a database that is neither the backup nor the previous state, and the
application's caches will additionally describe data that no longer exists.

```bash
# 1. Stop writers. Leave mongo running — the restore needs it.
docker compose stop backend

# 2. Take a safety backup of the CURRENT state, whatever state it is in.
#    You are about to overwrite the only copy of the evidence.
./scripts/backup/backup_mongo.sh --tier daily --no-prune

# 3. Choose an artifact and verify it (restore_mongo.sh also does this,
#    but do it consciously — it is cheap and it is the decision point).
./scripts/backup/verify_backup.sh --latest --level structural

# 4. Restore.
./scripts/backup/restore_mongo.sh --latest --drop      # full replace, prompts
#   or, to inspect before committing to it:
./scripts/backup/restore_mongo.sh --latest --target-db alpha_stock_inspect --yes

# 5. Restart and confirm the application agrees with the database.
docker compose start backend
curl -fsS localhost:8000/api/health/ready

# 6. Flush the cache. It describes the pre-restore database.
docker compose exec redis redis-cli FLUSHALL
```

Step 6 is not optional and is the step most restore runbooks omit: Redis holds
cached views of data that has just been replaced. Flushing costs a few minutes
of warm-up (§6) and prevents users seeing a coherent-looking blend of two
different database states.

### 9.2 The four rules the script enforces for you

1. **Verify before writing.** The one unrecoverable ordering mistake is to drop
   a live collection and then discover the archive is corrupt — the bad data you
   were replacing is now gone too.
2. **The default is a merge, not a replace.** Without `--drop`, existing `_id`s
   are kept and only missing documents are inserted. "Insert what is missing" is
   recoverable; "replace everything" is not.
3. **Confirmation is typed, not a keystroke.** A `y/N` prompt at 03:00 is
   answered by muscle memory. Retyping the database name is the same protocol
   `terraform destroy` uses. `--yes` exists for automation, and says so loudly.
   Restoring into an *empty* database — the disaster-recovery case — needs no
   confirmation, because there is nothing to lose.
4. **Verify after writing.** `mongorestore` exits 0 on a restore that inserted
   nothing. The count comparison against the manifest baseline is what catches
   that.

---

## 10. Configuration & secret recovery

A restored MongoDB is useless to an application that cannot start.

| Lost | Consequence |
|---|---|
| `JWT_SECRET` | Every session token ever issued is invalid. Every user logged out at once. |
| `BROKER_TOKEN_KEY` | Every stored broker token in the database is **permanently undecryptable**. Every user must re-link their broker. |
| `MONGO_APP_PASSWORD` | The backend cannot authenticate to the database it just restored. |
| A provider API key | A support ticket and a wait, per provider. |

**The encryption keys are part of the data.** A strategy that protects the
database but not the key material protects the ciphertext and throws away the
key.

```bash
BACKUP_ENCRYPTION_PASSPHRASE_FILE=/etc/stockassist/backup.key \
  ./scripts/backup/backup_config.sh

./scripts/backup/backup_config.sh --list    # see what would be included
```

* **Encryption is mandatory here with no development exemption.** This archive
  is 100 % credential material; there is no environment where writing it in
  plaintext is right, so there is no flag for it.
* **Tracked files are excluded on purpose.** `docker-compose*.yml`, Dockerfiles,
  `redis.conf`, `*.example` — all in git. `git clone` restores them at the exact
  reviewed revision; a backup copy restores them at whatever they happened to be.
  The manifest records the **git commit** instead, so a recovery checks out that
  revision and unpacks the secrets over it.
* **The manifest lists file names, never contents or per-file hashes.** A
  per-file hash would let anyone holding the (unencrypted) manifest confirm a
  guessed secret offline.

### ⚠ The recursive-dependency trap

> The passphrase that encrypts this archive **cannot** be stored in this archive,
> in the repository, in the deployment's environment, in this deployment's
> secret store, or on the host being backed up. **Every one of those is
> unavailable in the disaster this backup exists for.**
>
> It belongs in an **offline escrow** that survives the loss of the entire
> deployment: a password manager owned by a person, a printed copy in a safe, or
> a cloud KMS in a different account with different credentials. **At least two
> people must be able to reach it**, or the recovery plan has a single point of
> failure with a pulse.

*"We could not decrypt our backups because the key was in the vault that was
down"* is one of the most common ways a tested backup strategy still fails.

### Restoring configuration

```bash
# 1. The manifest is plaintext — read the commit the secrets were captured at.
grep git_commit config-<ts>.manifest.json

# 2. Clone and check out that exact revision, so code and configuration agree.
git clone <repo> && cd <repo> && git checkout <that-commit>

# 3. Unpack the secrets over it. Paths inside the archive are
#    repository-relative, so no path surgery is needed.
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha512 \
  -pass file:/path/to/escrowed.key -in ../config-<ts>.tar.gz.enc | tar -xzf -

# 4. Restore the permissions tar does not carry across every platform.
chmod 700 secrets && chmod 600 secrets/* .env
```

---

## 11. Schedule

Cron on the Docker host. `MAILTO` is the interim failure channel until PH2.10
wires alerting to a monitored destination.

```cron
MAILTO=ops@yourdomain
BACKUP_ENCRYPTION_PASSPHRASE_FILE=/etc/stockassist/backup.key
BACKUP_ROOT=/srv/backups/stockassist

# Nightly full backup + automatic structural verification, then off-host sync.
# 03:15 UTC — outside every market session this platform serves.
15 3 * * *  cd /srv/stockassist && ./scripts/backup/backup_mongo.sh && rclone sync "$BACKUP_ROOT" remote:stockassist --immutable

# Uploads, once they are mounted (no-op and exit 0 until then).
35 3 * * *  cd /srv/stockassist && ./scripts/backup/backup_uploads.sh

# Configuration: weekly, and MANUALLY after every secret rotation.
50 3 * * 0  cd /srv/stockassist && ./scripts/backup/backup_config.sh

# Cheap integrity sweep over every retained artifact.
30 4 * * *  cd /srv/stockassist && ./scripts/backup/verify_backup.sh --all --level checksum

# THE DRILL. First of the month. This is the line that makes the rest true.
0 5 1 * *   cd /srv/stockassist && ./scripts/backup/verify_backup.sh --latest --level drill
```

The tier is chosen automatically from the UTC calendar: the 1st → `monthly`,
Sundays → `weekly`, everything else → `daily`. One cron line, three tiers.

---

## 12. RPO and RTO

| | Target | Basis |
|---|---|---|
| **RPO** | **≤ 24 h** | Nightly full backup. Up to 24 h of writes are lost in a total-loss scenario. Reducing this requires point-in-time recovery — §14. |
| **RTO** | **≤ 4 h** | Measured restore is *seconds* at current data volume (§8); the four hours are dominated by human time — detection, decision, provisioning a host, fetching the artifact from off-host storage, restoring configuration, and post-restore validation. |

These match `.claude/PRODUCTION_HARDENING.md` §11 and are now **backed by a
measured drill** rather than an estimate. Re-measure at production data volume
before treating the RTO as committed (§8.1).

---

## 13. Disaster scenarios

| # | Scenario | Response | Expected recovery |
|---|---|---|---|
| 1 | Accidental `dropDatabase` / bad migration | §9 checklist with `--drop` from the most recent nightly | Minutes + up to 24 h data loss |
| 2 | A single collection corrupted | Restore to a scratch db (`--target-db`), copy the one collection across, leave the rest live | Minutes, no other data affected |
| 3 | `mongo_data` volume lost, host intact | `docker compose up -d mongo` (empty), then §9 from step 3 | Minutes + up to 24 h data loss |
| 4 | Host lost entirely | New host → `git clone` at the manifest's commit → restore config (§10) → `docker compose up -d mongo` → restore Mongo → start backend | Hours; bounded by off-host fetch |
| 5 | Redis volume lost | Nothing. Restart Redis; the cache refills. §6 | Seconds + a few minutes of warm-up |
| 6 | Backup found corrupt during a restore | The verify-before-write guard means nothing was written. Step back one artifact and retry; each tier is an independent restore point | Minutes; this is what having 17 artifacts is for |
| 7 | **Passphrase lost** | **Nothing can be done.** Every encrypted artifact is permanently unreadable. This is why §10's escrow requirement has two people in it | Unrecoverable |
| 8 | Ransomware reaches the host | Restore from the off-host, object-locked copy onto clean infrastructure. A backup directory on the compromised host must be assumed compromised too | Hours; entirely dependent on §4 |

---

## 14. Known limitations

**L1 — No point-in-time recovery; cross-collection consistency is not
guaranteed.** `mongodump` against a standalone mongod is per-collection
consistent only (§5.3). RPO is therefore bounded by backup frequency, not by
seconds. *Fix:* convert mongod to a single-node replica set, which enables
`mongodump --oplog` and true PITR. That is a compose and connection-string
change with a restart, deliberately out of scope for PH2.9.

**L2 — Off-host storage is documented, not implemented.** The scripts write
locally. The `rclone` line in §11 is the intended pattern and is unverified in
this environment. Until it runs, a single host failure loses both the database
and its backups.

**L3 — AES-CBC is not authenticated.** Accidental corruption is detected;
deliberate tampering by someone who can rewrite both artifact and manifest is
not (§5.6). *Mitigation today:* object lock on the backup destination.
*Fix later:* `age` or GPG, introduced as a new `encryption` value so existing
artifacts stay readable.

**L4 — Backup failure is not alerted.** The scripts exit non-zero and explain
why; nothing routes that to a monitored channel yet. Cron `MAILTO` is the
interim. **PH2.10** owns the alert.

**L5 — The drill is manual/cron, not CI.** CI has no MongoDB, so the 39 tests in
`backend/tests/test_backup_restore.py` exercise the scripts against stubbed mongo
tools. The real end-to-end drill is an operational procedure (§11) whose evidence
is §8, not an automated gate.

**L6 — `docker` mode is unverified in this environment.** No Docker daemon was
available during PH2.9. Every measurement in §8 was taken in `BACKUP_MODE=direct`
against a real MongoDB 8.0.13. The `docker` transport differs only in *how* the
mongo tools are invoked (`docker compose exec -T`); the stream, encryption,
manifest, retention and verification paths are the identical code. **Run one
`--level drill` in `docker` mode before relying on it**, and see §15.

**L7 — Uploads have no data and therefore no executed drill.** The volume is
declared but not mounted (§7). The host-path mode was exercised end to end
(backup → restore → byte comparison); the Docker-volume mode shares its code but
has not been run.

**L8 — `docker compose down -v` still destroys everything local.** It removes
`mongo_data`, `redis_data` and `backend_logs`. Backups in `BACKUP_ROOT` survive
it only because `BACKUP_ROOT` is not a Docker volume — which is another reason
§4's "different filesystem" rule matters.

---

## 15. First run in a Docker deployment

Because of **L6**, do this once before the schedule in §11 is trusted:

```bash
# 1. Operator credentials must be present. The backup uses the ROOT user,
#    not the application user: the app user is readWrite on one database by
#    design (docker/mongodb/init-app-user.js) and cannot do operator work.
#    This is exactly why PH2.2 kept the root password out of the backend.
export MONGO_ROOT_USERNAME=… MONGO_ROOT_PASSWORD=…
export BACKUP_ENCRYPTION_PASSPHRASE_FILE=/etc/stockassist/backup.key

# 2. Take one backup.
./scripts/backup/backup_mongo.sh --tier daily

# 3. Prove it restores. This is the acceptance test.
./scripts/backup/verify_backup.sh --latest --level drill
```

If step 3 prints `drill OK`, the backup system is live. If it does not, nothing
about the schedule matters yet.

---

## 16. Configuration reference

Ordinary environment variables read by the scripts. They are deliberately **not**
in `backend/security/secrets.py`: that registry is the *application's*
configuration surface, validated fail-closed at boot and mirrored into
`.env.example`. These are host-operations settings that the application neither
reads nor should fail to start without.

| Variable | Default | Purpose |
|---|---|---|
| `BACKUP_ROOT` | `<repo>/backups` | Where artifacts are written. **Change this in production** — §4. |
| `BACKUP_MODE` | `docker` | `docker` (tools run in the mongo container) or `direct` (tools run on this host against `MONGO_URL`). |
| `BACKUP_ENCRYPTION_PASSPHRASE_FILE` | — | Path to the passphrase. **Preferred** over the inline form. |
| `BACKUP_ENCRYPTION_PASSPHRASE` | — | Inline passphrase. Visible in `/proc/<pid>/environ` and inherited by children — use the file form. |
| `MONGO_DB_NAME` | `alpha_stock` | Database to back up. |
| `MONGO_ROOT_USERNAME` / `MONGO_ROOT_PASSWORD` | — | Operator credentials, required in `docker` mode. |
| `MONGO_URL` | — | Required in `direct` mode. |
| `BACKUP_RETAIN_DAILY` / `_WEEKLY` / `_MONTHLY` | 7 / 4 / 6 | Per-tier keep counts. Must be ≥ 1. |
| `BACKUP_RETAIN_CONFIG` | 20 | Config-archive versions kept. |
| `BACKUP_COMPOSE_FILE` | `<repo>/docker-compose.yml` | Compose file used by `docker` mode. |
| `BACKUP_MONGO_SERVICE` | `mongo` | Compose service name. |
| `BACKUP_UPLOADS_VOLUME` | `stockassist_backend_uploads` | Named volume for uploads. |
| `BACKUP_HELPER_IMAGE` | `redis:${REDIS_IMAGE_TAG:-7.2-alpine}` | Image used to read the uploads volume. Pinned, already present. |
| `BACKUP_CONFIG_SOURCE_ROOT` | `<repo>` | Root the config backup reads secret files from. |
| `APP_ENV` | `production` | `production` makes encryption mandatory for database dumps. |

The repository `.env` is also read — **parsed, never `source`d**. `source .env`
is the obvious implementation and it is a remote-code-execution primitive: a line
like `FOO=$(rm -rf /)` in a file half the team can write executes as whoever runs
the backup, which is root on most hosts. An explicitly-set environment variable
always beats the file.

---

## 17. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `refusing to write an UNENCRYPTED backup in APP_ENV=production` | No passphrase configured | Set `BACKUP_ENCRYPTION_PASSPHRASE_FILE`. Working as designed. |
| `DECRYPTION FAILED … does not match` | Wrong passphrase, or a passphrase file with a stray trailing newline written by a different tool | The scripts strip one trailing newline; check the escrowed value byte for byte. |
| `NOT A MONGODUMP ARCHIVE` | The artifact is valid gzip but not a dump — usually a captured error message | Check the dump credentials and re-run; the artifact is already quarantined. |
| `manifest schema N != 1` | Artifact written by a newer version of these scripts | Use matching script versions; the refusal is deliberate. |
| `could not stage the mongo tools config inside the 'mongo' container` | The stack is not running | `docker compose up -d mongo`. |
| `destructive operation … requires an interactive terminal` | Unattended restore into a populated database | Add `--yes` if that is genuinely intended. |
| Backup succeeds but `collections` is `{}` | `mongosh` unreachable when the baseline was sampled | Backup is still valid but **cannot be drilled**. Fix mongosh access and take another. |
| `retention count must be >= 1` | Empty or non-numeric `BACKUP_RETAIN_*` | Fix the variable. Nothing was deleted. |

---

## 18. See also

* [`docs/infrastructure/REDIS.md`](../infrastructure/REDIS.md) — persistence
  configuration and the reasoning behind AOF-on/RDB-off
* [`docs/deployment/DOCKER_COMPOSE.md`](../deployment/DOCKER_COMPOSE.md) — volumes, networks, service topology
* [`docs/deployment/SECRETS.md`](../deployment/SECRETS.md) — how secrets are provisioned in the first place
* [`docs/operations/runbooks.md`](runbooks.md) · [`incident-response.md`](incident-response.md)
* `.claude/PRODUCTION_HARDENING.md` §11 — RPO/RTO and the recovery strategy

---

## 19. Document history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-08-04 | Created by PH2.9 — Production Backup & Restore. |

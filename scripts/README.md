# scripts/

Host-side operational scripts — things an **operator** runs on the machine that
hosts the deployment, not things the application runs.

That boundary is the reason this directory exists separately from
`backend/scripts/`:

| | `scripts/` | `backend/scripts/` |
|---|---|---|
| Runs on | the Docker host | inside the backend image |
| Language | POSIX-ish `bash` | Python |
| Reads config from | environment + the repository `.env` | `backend/security/secrets.py` |
| Needs the app installed? | no | yes |
| Examples | backup, restore, verification | `seed_dev_admin.py`, `generate_env_example.py`, `audit_dependencies.py` |

A backup script that could only run inside the application image would be
unusable in the one situation it exists for — a host where the application will
not start.

## Contents

### `backup/` — backup, restore and verification (PH2.9)

| Script | Purpose |
|---|---|
| `lib.sh` | Shared library. **Sourced, never executed.** |
| `backup_mongo.sh` | Full MongoDB backup → manifest → retention → self-verify. |
| `restore_mongo.sh` | Restore, with verify-before-write and verify-after-write. |
| `verify_backup.sh` | `checksum` / `structural` / `drill` verification levels. |
| `backup_config.sh` | Encrypted archive of secret material. |
| `backup_uploads.sh` | Upload-volume backup and restore. |

Every one of them takes `--help`.

**Read [`docs/operations/BACKUP_AND_RESTORE.md`](../docs/operations/BACKUP_AND_RESTORE.md)
before running any of them in production** — particularly §4 (where backups must
live) and §10 (where the encryption passphrase must *not* live).

### `dr/` — disaster recovery (PH2.10)

| Script | Purpose |
|---|---|
| `dr_verify.sh` | Layered diagnosis **and** post-recovery verification: host → containers → data → application. Safe to run against a broken system. |
| `deploy_rollback.sh` | Deployment ledger (`record` / `list` / `current`) and verified rollback with an automatic revert. |

Both source `backup/lib.sh` rather than reimplementing configuration loading,
logging and the MongoDB transport — a verifier that reached the database
differently from the way the restore reaches it could report healthy against a
database the restore never touched.

```bash
./scripts/dr/dr_verify.sh --level full          # is it actually back?
./scripts/dr/deploy_rollback.sh rollback --previous
```

**Read [`docs/operations/DISASTER_RECOVERY.md`](../docs/operations/DISASTER_RECOVERY.md)**
— §7 is the runbooks, §9 is what each verification layer proves, §13 is the
checklist of things that must be true *before* an incident.

## Conventions for anything added here

* `set -euo pipefail`, and a header block explaining **why the file exists**
  before **what it does**.
* **Bash 3.2 compatible.** macOS ships bash 3.2 while production runs bash 5.x,
  and a script that only works on one of them is a script whose behaviour is
  first observed during an incident. No `declare -A`, no `mapfile`, no `${x^^}`.
* Paths resolved from `${BASH_SOURCE[0]}`, never from `$PWD` — cron runs with
  `$PWD` set to the crontab owner's home directory.
* Human output to **stderr**; machine-readable output to **stdout**, so a
  script can be both read by a person and consumed by another script.
* Exit `1` for a real failure, `2` for a usage error, so monitoring can alert on
  one and ignore the other.
* Anything destructive confirms, and offers an explicit `--yes` for automation.

# Runbooks

Operational runbooks for StockAssist AI live in
**[`DISASTER_RECOVERY.md`](DISASTER_RECOVERY.md) §7**, next to the recovery
objectives, escalation matrix and verification checklist they depend on.

They were not left here as a separate list on purpose: a runbook read in
isolation from its RPO/RTO and its verification step is how a recovery gets
executed correctly and declared finished incorrectly.

| # | Scenario |
|---|---|
| R1 | Failed deployment → rollback |
| R2 | Backend container down or restart-looping |
| R3 | Redis failure or data loss |
| R4 | MongoDB corruption, bad migration, accidental data destruction |
| R5 | The rollback also failed / neither version is healthy |
| R6 | Volume or storage failure (host intact) |
| R7 | Complete server loss |
| R8 | Configuration corruption or accidental secret rotation |
| R9 | Suspected compromise |
| R10 | Backup job failing (no outage yet) |

**Start here in any incident:**

```bash
./scripts/dr/dr_verify.sh --level full     # which layer failed → which runbook
```

See also: [`incident-response.md`](incident-response.md) ·
[`BACKUP_AND_RESTORE.md`](BACKUP_AND_RESTORE.md) ·
[`MONITORING.md`](MONITORING.md)

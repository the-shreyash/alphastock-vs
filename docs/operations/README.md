# Operations

## Purpose
To document how the system is deployed, scaled, hardened, and maintained in production environments.

## Contents
- Deployment Guides
- Production Hardening Plans
- Migration Plans
- Incident Response and Runbooks
- [Monitoring & Observability](MONITORING.md) — health probes, metrics, structured
  logging, request correlation, and the operational troubleshooting guide
- [Logging Infrastructure](LOGGING.md) — log streams, rotation, retention,
  compression, redaction policy, Docker logging drivers, and the path to
  centralized log aggregation
- [Backup & Restore](BACKUP_AND_RESTORE.md) — what is backed up and what is
  deliberately not, encryption, grandfather-father-son retention, the three
  verification levels and the restore drill, the restore procedure, secret
  recovery, disaster scenarios, and the measured RPO/RTO

## Who should read it
- DevOps Engineers
- Site Reliability Engineers (SREs)
- On-call Software Engineers

## Related documentation
- [System Architecture](../architecture/README.md)
- [Engineering Standards](../engineering/README.md)

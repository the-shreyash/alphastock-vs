# Architecture

## Purpose
To detail the system design, components, integrations, and architectural decisions.

## Contents
- System Architecture and Component Design
- Database Schemas
- API References
- Realtime Systems and Market Data Architecture
- [Security Modules](SECURITY_MODULES.md) — every module in `backend/security/`

Cross-cutting backend packages, each with exactly one authoritative
implementation of its concern:

| Package | Documented in |
|---|---|
| `backend/security/` | [SECURITY_MODULES.md](SECURITY_MODULES.md) |
| `backend/observability/` | [MONITORING.md](../operations/MONITORING.md) — health probes, metrics, structured logging, request correlation |
| `backend/infrastructure/` | [REDIS.md](../infrastructure/REDIS.md) — connections to backing services: pooling, retry, circuit breaking, Pub/Sub reconnect |

## Who should read it
- Software Engineers and Architects
- Technical Product Managers
- Systems Engineers

## Related documentation
- [Operations and Deployment](../operations/README.md)
- [Engineering Standards](../engineering/README.md)

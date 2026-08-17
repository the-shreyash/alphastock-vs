# Architecture

## Purpose
To detail the system design, components, integrations, and architectural decisions.

## Contents
- System Architecture and Component Design
- Database Schemas
- API References
- Realtime Systems and Market Data Architecture
- [Security Modules](SECURITY_MODULES.md) — every module in `backend/security/`
- [Observability](OBSERVABILITY.md) — the observability architecture, error
  classification, metric cardinality rules, sensitive-data enforcement, the
  alert catalogue, and the incident troubleshooting flow
- [Analytics & Data Integrity](ANALYTICS.md) — every number the product
  displays, classified REAL / DERIVED / MOCK / UNAVAILABLE; the source-of-truth
  model, financial metric semantics (gross basis, sign conventions, partial
  exits, flow adjustment), the IST time-window strategy, data-quality rules,
  and the record of the mock-removal sprint. **No metric is classified MOCK**
  (4 REAL / 32 DERIVED / 0 MOCK / 17 UNAVAILABLE); §11 says what happened to
  each of the seventeen that used to be, and §10.4 names what every remaining
  unavailable metric would need to become answerable

Cross-cutting backend packages, each with exactly one authoritative
implementation of its concern:

| Package | Documented in |
|---|---|
| `backend/security/` | [SECURITY_MODULES.md](SECURITY_MODULES.md) |
| `backend/observability/` | [OBSERVABILITY.md](OBSERVABILITY.md) — architecture and rules (read before adding an instrument); [MONITORING.md](../operations/MONITORING.md) — operator's manual; [LOGGING.md](../operations/LOGGING.md) — log infrastructure |
| `backend/infrastructure/` | [REDIS.md](../infrastructure/REDIS.md) — connections to backing services: pooling, retry, circuit breaking, Pub/Sub reconnect |
| `backend/analytics/` | [ANALYTICS.md](ANALYTICS.md) — time windows and the timezone strategy, the metric provenance contract, the metric inventory, and source-data quality checks |

## Who should read it
- Software Engineers and Architects
- Technical Product Managers
- Systems Engineers

## Related documentation
- [Operations and Deployment](../operations/README.md)
- [Engineering Standards](../engineering/README.md)

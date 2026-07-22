# Deployment

## Purpose
To document how StockAssist AI is packaged, containerized, configured and shipped to a running environment. This folder covers the *mechanics* of deployment — build artifacts, container architecture, runtime configuration and health contracts.

## Contents
- [DOCKER.md](DOCKER.md) — Backend production container architecture: multi-stage build strategy, container security posture, runtime configuration, entrypoint and health-check design, build/run instructions, troubleshooting.

## Who should read it
- Platform and DevOps Engineers
- Backend Engineers preparing a release
- Anyone running the stack outside a development machine

## Related documentation
- [Operations](../operations/README.md) — production checklist, release checklist, runbooks, incident response
- [Architecture](../architecture/README.md) — what is being deployed
- [Security](../security/PH1_CERTIFICATION.md) — the application-level controls the container carries
- `.claude/PRODUCTION_ROADMAP.md` — the PH2 infrastructure plan this folder is built out against
- `production.env.example` (repository root) — the operator-facing runtime environment template

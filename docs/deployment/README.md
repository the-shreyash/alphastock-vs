# Deployment

## Purpose
To document how StockAssist AI is packaged, containerized, configured and shipped to a running environment. This folder covers the *mechanics* of deployment — build artifacts, container architecture, runtime configuration and health contracts.

## Contents
- [DOCKER.md](DOCKER.md) — Backend production container architecture: multi-stage build strategy, container security posture, runtime configuration, entrypoint and health-check design, build/run instructions, troubleshooting.
- [DOCKER_COMPOSE.md](DOCKER_COMPOSE.md) — Service orchestration: the production-shaped backend/MongoDB/Redis stack and its development overlay. Network segmentation, volume design, the two-file environment split, startup ordering via health checks, measured startup timings, known limitations, troubleshooting.
- [SECRETS.md](SECRETS.md) — Production secrets architecture: how a credential reaches the code that uses it. Docker Secrets, the `_FILE` convention, the central loader and its precedence order, boot-time validation, rotation and blast radius per secret, and the migration path to Swarm / Kubernetes / cloud secret managers.

## Who should read it
- Platform and DevOps Engineers
- Backend Engineers preparing a release
- Anyone running the stack outside a development machine

## Related documentation
- [Operations](../operations/README.md) — production checklist, release checklist, runbooks, incident response
- [Architecture](../architecture/README.md) — what is being deployed
- [Security](../security/PH1_CERTIFICATION.md) — the application-level controls the container carries
- `.claude/PRODUCTION_ROADMAP.md` — the PH2 infrastructure plan this folder is built out against
- `production.env.example` (repository root) — the backend container's runtime environment template
- `compose.env.example` (repository root) — the Docker Compose stack's own variable template (infrastructure credentials, host ports, image tags)
- `secrets/README.md` (repository root) — the host-side Docker secret files and the generator that creates them
- `.claude/SECRETS.md` — the secret *inventory* (every variable, sensitivity, rotation policy, incident runbook); [SECRETS.md](SECRETS.md) here is the *mechanism*

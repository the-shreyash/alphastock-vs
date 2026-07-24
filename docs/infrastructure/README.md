# Infrastructure

Backing services the application runs on: how each one is configured, how the
backend connects to it, and how each recovers when it fails.

The line between this folder and its neighbours:

| Folder | Answers |
|---|---|
| **infrastructure/** | *This* service — its configuration, connection lifecycle, failure modes, and the path to scaling it |
| [deployment/](../deployment/README.md) | How the whole stack is built, wired and shipped |
| [operations/](../operations/README.md) | How the running system is observed and what to do at 3am |

## Documents

| Document | Covers | Sprint |
|---|---|---|
| [CONFIGURATION.md](CONFIGURATION.md) | The single configuration entry point — sources & precedence, the four environment profiles, fail-closed validation, dependency management, and image-footprint optimization | PH2.8 |
| [REDIS.md](REDIS.md) | Shared cache and cross-process realtime fan-out — server tuning, connection pooling, circuit breaking, Pub/Sub reliability, monitoring, and the Sentinel/Cluster migration path | PH2.7 |

MongoDB has no document here yet; its topology is currently covered by
[deployment/DOCKER_COMPOSE.md](../deployment/DOCKER_COMPOSE.md) and its schema by
[architecture/](../architecture/README.md).

## Code

| Path | Role |
|---|---|
| `backend/infrastructure/` | The only place the backend opens a connection to a backing service |
| `docker/redis/redis.conf` | Redis server configuration, with the rationale for every setting inline |
| `docker/mongodb/` | MongoDB bootstrap |

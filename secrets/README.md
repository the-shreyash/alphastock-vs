# `secrets/` — host-side secret files

This directory holds the files that `docker-compose.secrets.yml` mounts into
containers as Docker secrets. **Everything in it except this README, `.gitignore`
and `generate.sh` is ignored by git and must never be committed.**

## Create them

```bash
./secrets/generate.sh          # generate anything missing; never overwrites
./secrets/generate.sh --check  # report what exists, write nothing
./secrets/generate.sh --rotate # regenerate the values the script owns
```

Then bring the stack up with the overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d
```

## How a file here reaches the application

```
./secrets/jwt_secret                 (host, chmod 600, git-ignored)
        │  docker-compose.secrets.yml  secrets: { jwt_secret: { file: … } }
        ▼
/run/secrets/jwt_secret              (in-container, read-only)
        │  JWT_SECRET_FILE=/run/secrets/jwt_secret
        │  — or auto-discovered by name, no variable needed
        ▼
security/secrets.py  load_secrets()  →  os.environ["JWT_SECRET"]
        ▼
security/jwt.py · security/csrf.py · services/*
```

The filename must be the environment variable's name, lowercased
(`jwt_secret` → `JWT_SECRET`). That is what makes auto-discovery work without
any per-secret wiring. Only names in `SECRET_REGISTRY` are auto-discovered — a
stray file here cannot invent a new environment variable.

## What this does and does not protect

| | Before (env file) | With this overlay |
|---|---|---|
| Visible in `docker inspect` | yes | no (a path is shown instead) |
| In the container's environment | yes | no |
| Inherited by child processes | yes | no |
| Plaintext on the host disk | yes | **yes** — this is the residual risk |

Under Docker **Swarm** the same overlay works with `external: true` secrets held
in the encrypted Raft store and mounted from tmpfs — at which point the last row
becomes "no". See [`docs/deployment/SECRETS.md`](../docs/deployment/SECRETS.md)
§7 for that path and for the Kubernetes / cloud-secret-manager migrations.

## If you think one of these leaked

Treat the whole directory as compromised, not just the one file — anything that
could read one could read all of them. Rotation procedure and blast radius per
secret: `docs/deployment/SECRETS.md` §6, and the incident runbook in
`.claude/SECRETS.md` §9.

# Production Secrets Architecture

**Sprint:** PH2.3 — Production Secrets Management
**Status:** Implemented
**Scope:** How a credential travels from wherever it is stored to the code that uses it — the resolution order, the file-backed sources, the validation applied on the way, and the rotation story. Application logic, authentication behaviour, CI/CD (PH2.4/2.5) and external secret managers (Vault, AWS/Azure/GCP) are deliberately *not* covered; §7 documents the migration path to the last of those without implementing it.

**Authoritative companions:** [`.claude/SECRETS.md`](../../.claude/SECRETS.md) is the *inventory* — every variable, its sensitivity, its rotation policy, and the incident runbook. This document is the *mechanism*. `backend/security/secrets.py` is the code that implements both, and is the single source of truth if the three ever disagree.

---

## 1. What problem this solves

Before PH2.3, every credential this platform uses reached the application the same way: as a plaintext environment variable. That is the default because it is the path of least resistance — `docker run -e`, `env_file:`, a `.env` on disk — and it has four exposures that no amount of care removes:

| Exposure | Why it happens |
|---|---|
| `docker inspect stockassist-backend` prints every secret | The container's environment is metadata, readable by any host user in the `docker` group and by anything that can reach the Docker socket |
| `/proc/<pid>/environ` | Readable by the process itself and by root — so any code execution in the container reads every secret, whether or not that code was ever meant to touch them |
| Inherited by every child process | A subprocess spawned for an unrelated reason carries the AI keys, the broker secrets, and the database URI |
| Captured by crash reporters, APM agents, and `docker compose config` output | Environment capture is a *feature* of those tools; they cannot tell a secret from a setting |

None of these is a vulnerability in the application. They are properties of the delivery mechanism, and the fix is to change the mechanism — which is what this sprint did, without changing a single line of application logic.

A file is different in each of those four rows: it is readable only by a process that opens that specific path, it is not inherited, it does not appear in container metadata, and — for Docker Swarm and Kubernetes secrets — it lives in a tmpfs that never touches any node's disk.

---

## 2. Architecture

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │  SOURCES                                                             │
   │                                                                      │
   │   Docker secret        Kubernetes         `<NAME>_FILE`      plain   │
   │   /run/secrets/x       projected volume   pointer            env var │
   │        │                    │                  │                │    │
   └────────┼────────────────────┼──────────────────┼────────────────┼────┘
            │                    │                  │                │
            └────────────────────┴──────────┬───────┴────────────────┘
                                            ▼
            ┌───────────────────────────────────────────────────────┐
            │      CENTRAL SECRET LOADER                            │
            │      backend/security/secrets.py                      │
            │                                                       │
            │   resolve_all()   one precedence order, every var     │
            │   load_secrets()  materialize into os.environ         │
            │   validate_config()  fail closed on anything invalid  │
            └───────────────────────────┬───────────────────────────┘
                                        │  os.environ (hydrated, once)
                                        ▼
            ┌───────────────────────────────────────────────────────┐
            │      APPLICATION COMPONENTS                           │
            │                                                       │
            │   security.jwt      security.csrf    security.cookies │
            │   security.recovery security.cors    security.headers │
            │   services.cache    services.claude_provider          │
            │   services.brokers.*   services.email_service   …      │
            │   server.py                                           │
            └───────────────────────────────────────────────────────┘
```

### Why the loader materializes into `os.environ`

About thirty modules already read their configuration through small call-time resolvers — `os.environ.get("ANTHROPIC_API_KEY", "").strip()` inside a function, evaluated on each call. The loader resolves every input from its highest-precedence source and writes the result into `os.environ` **once**, before any of those modules is imported.

That single decision is what makes this sprint a *zero-call-site* change:

- Every existing consumer transparently gains Docker-secret support without being modified. No 30-file refactor, therefore no regression risk in security-critical paths.
- There is still exactly one place that knows how to find a secret. The alternative — an accessor that every module must remember to use — creates a rule that can be forgotten, and the first module that forgets it silently loses file support.
- Because those resolvers read at *call* time rather than capturing at import, re-running the loader propagates a rotated value to live code (§6).

The trade-off is honest and worth stating: after hydration, a file-sourced secret **is** in `os.environ`, so `/proc/<pid>/environ` inside the container still shows it. What changes is that it is no longer in the container's *declared* environment — `docker inspect` shows `JWT_SECRET_FILE=/run/secrets/jwt_secret`, a path, not a credential — and it is no longer visible to anything reading container metadata from the host. That is the larger and more commonly exploited surface.

---

## 3. Loading order and precedence

For every variable `NAME`, highest precedence first:

| # | Source | Form | Notes |
|---|---|---|---|
| 1 | Explicit pointer | `NAME_FILE=/path/to/file` | Highest, because it is an unambiguous operator instruction. Works for **any** variable, registered or not |
| 2 | Discovered mount | `$SECRETS_DIR/NAME` or `$SECRETS_DIR/name` | `SECRETS_DIR` defaults to `/run/secrets`. Both cases are checked — Compose/Swarm names are conventionally lowercase, Kubernetes keys usually are not. Limited to names in `SECRET_REGISTRY` |
| 3 | Plaintext environment | `NAME=value` | The development fallback, and why an existing `.env` workflow keeps working untouched |
| 4 | — | absent | Then the validator decides whether that is fatal for this environment |

### The rules that make this fail closed

**An unreadable pointer never falls back.** If `JWT_SECRET_FILE` names a file that does not exist, is a directory, is empty, is over 64 KB, or is not UTF-8, resolution **errors** — it does not quietly use the `JWT_SECRET` environment variable instead. This is the single most important property in the module. A silent downgrade is exactly how a rotation appears to succeed while the application keeps signing with the old key.

**Two sources for one secret is an error, not a merge.** A file source competing with a plaintext environment variable means two people (or two layers of a deployment) each believe they own that value. Picking one silently lets a rotated secret be shadowed by a stale one, which surfaces days later as authentication errors that point nowhere near the cause. The boot is refused with a message naming both sources.

```
✗ MONGO_URL is supplied by more than one source (file(/run/secrets/mongo_url), env).
  Exactly one source must own a secret — remove the others so a rotation cannot be
  shadowed by a stale value.
```

**One deliberate exception:** a successful `_FILE` pointer *suppresses* discovery rather than competing with it. `JWT_SECRET_FILE=/run/secrets/jwt_secret` names precisely the path discovery scans — the normal Docker-secrets layout — so treating them as rivals would make the documented configuration fail on every boot. Comparing paths instead would still be wrong on a case-insensitive filesystem (macOS, Windows), where `JWT_SECRET` and `jwt_secret` are one file under two names. An explicit pointer is unambiguous; once it resolves there is nothing left to disambiguate.

**Discovery cannot invent variables.** Only names in `SECRET_REGISTRY` are auto-discovered. A stray file in the secrets directory must never be able to create an environment variable — otherwise write access to a mount becomes arbitrary configuration injection.

**Whitespace is stripped.** Essentially every tool that writes a secret file appends a trailing newline (`echo`, every text editor, `kubectl create secret --from-file`). A JWT signed with `"key\n"` fails to verify against `"key"`, and the symptom looks nothing like the cause. This matches the existing behaviour for environment values, so one rule covers both sources.

---

## 4. Validation

`validate_config()` runs at startup — from `server.py` at import time, and again as a dry run from `docker/entrypoint.sh` so the operator sees a clean aggregated report instead of a traceback from inside uvicorn. Every problem is collected and reported **together**: an operator fixes the whole environment in one pass rather than one variable per crash-loop.

### What is checked

| Class | Rule | Production | Development |
|---|---|---|---|
| **Presence** | Required-in-this-environment variables must be set | error | error for the core trio (`MONGO_URL`, `DB_NAME`, `JWT_SECRET`), else warning |
| **Sources** | Unreadable / empty / oversized / non-UTF-8 secret file; two competing sources | error | error |
| **Placeholders** | `changeme`, `your_…_here`, `REPLACE_ME`, `admin123`, … | error | warning |
| **Length** | Signing secrets ≥ 32 characters | error | error for `JWT_SECRET`, else warning |
| **Entropy** | < 8 chars, ≤ 4 distinct characters, < 8 distinct at ≥ 16 chars, or a keyboard/digit run | error | warning |
| **Mongo credentials** | `MONGO_URL` must carry `user:password` | error | warning |
| | `MONGO_URL` on a loopback host | warning | — |
| **Redis password** | `REDIS_URL`, if set, must carry a password | error | warning |
| **Encryption keys** | `BROKER_TOKEN_KEY` must be a valid Fernet key (44-char urlsafe-base64 of 32 bytes) | error | **error** |
| **OAuth** | `GOOGLE_CLIENT_ID`/`SECRET` both-or-neither | error | warning |
| | Client id shaped like `*.apps.googleusercontent.com` | warning | — |
| **API keys** | At least one AI provider configured | error | — |
| | `ANTHROPIC_API_KEY` starts with `sk-ant-` | warning | — |
| **Delivery posture** | A *sensitive* secret arriving as plaintext env | warning (error under `REQUIRE_FILE_SECRETS`) | — |
| **Dev bypasses** | `ENABLE_AUTO_LOGIN` must be off; no weak `ADMIN_PASSWORD` | error | — |

Two rules deserve their reasoning spelled out:

**The entropy check exists because the length check is gameable.** An operator who needs to get past "must be 32 characters" and types `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` passes it, and that key does not survive ten seconds of offline attack against a leaked JWT. The rules are deliberately conservative — a false positive blocks a production boot — so each one describes a value no generator would ever emit. A real `secrets.token_urlsafe(48)` has ~50 distinct characters and clears all of them by a wide margin.

**The Fernet check is an error even in development.** An invalid encryption key does not fail at boot; it fails the first time a user connects a broker account, which may be weeks after deployment and in a code path nobody was watching.

### Nothing is ever logged

The validator reports variable *names*, *sources*, and *presence* — never values. This is a property of construction, not of discipline: `ConfigReport` and `SecretResolution` hold names and booleans, `ResolvedSecret` excludes its value from `repr()`, and the aggregated error message is assembled from names alone. Tests assert it directly (`test_source_errors_never_contain_the_secret_value`, `test_conflict_error_names_the_sources_but_never_the_values`).

For the rare case a value must be referenced, `redact()` collapses it to a fixed mask and `fingerprint()` gives a 12-hex-character keyed HMAC — a *change detector* for rotation, explicitly not an anonymizer.

---

## 5. Workflows

### Development — nothing changes

Plaintext is the correct trade-off on a laptop, and the loader is a no-op for it:

```bash
cd backend && cp .env.example .env    # fill it in
python -m uvicorn server:app --reload
```

`APP_ENV=development` (the default) keeps the validator lenient: a half-filled `.env` still boots, and everything except the core trio degrades to a warning. Development is deliberately **not** nagged about plaintext delivery — a warning that fires on every laptop boot is a warning nobody reads in production either.

### Docker Compose — the base stack, unchanged

```bash
cp compose.env.example .env && cp production.env.example production.env   # fill both
docker compose -f docker-compose.yml up -d
```

Still the two-file split from PH2.2: `.env` carries infrastructure credentials for Compose interpolation, `production.env` carries application secrets for the backend container. See [DOCKER_COMPOSE.md](DOCKER_COMPOSE.md) §6.

### Docker Compose with Docker Secrets — the production posture

```bash
./secrets/generate.sh                    # create the host-side secret files
docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d
```

`./secrets/generate.sh` writes 48-byte CSPRNG values, `chmod 600`, git-ignored — and refuses to invent third-party credentials, since a placeholder Anthropic key is indistinguishable from a real one to everything downstream. `--check` reports status without writing; `--rotate` regenerates.

Note the overlay composes with `-f docker-compose.yml`, the explicit production path, not the developer default. Secret-file delivery is a production posture, so rehearsing it should rehearse the production stack.

**Migrating one secret at a time.** The overlay is designed for gradual adoption. Mount a secret named after the variable, lowercased, and the loader discovers it — no `_FILE` variable, no code change:

```yaml
# docker-compose.secrets.yml
services:
  backend:
    secrets: [anthropic_api_key]        # ← add
secrets:
  anthropic_api_key:
    file: ./secrets/anthropic_api_key   # ← add
```

You do **not** need to edit `production.env`. The overlay retracts the base values with an explicit empty string (`JWT_SECRET: ""`), which overrides `env_file` and, unlike null (`~`), does not inherit from the invoking shell — so retraction is identical on every machine.

**Locking the migration in.** Once every sensitive secret is file-backed, set `REQUIRE_FILE_SECRETS=true`. Plaintext delivery of a sensitive value becomes a boot error instead of a warning, so a regression cannot creep back in unnoticed. Leave it off until the migration is complete — with it on, the stack refuses to start while anything is still in `production.env`.

### Verifying it worked

```bash
docker compose logs backend | grep '\[secrets\]'
# [entrypoint] file-backed secrets: JWT_SECRET, MONGO_URL, REDIS_URL
# [secrets] env=production configured=9 file-backed=3 plaintext=6 warnings=5 errors=0

docker inspect stockassist-backend --format '{{json .Config.Env}}' | tr ',' '\n' | grep -i jwt
# "JWT_SECRET_FILE=/run/secrets/jwt_secret"   ← a path. Not a secret.
```

---

## 6. Rotation

The architecture was built so rotation needs no code change. What it cannot do is make rotation *consequence-free* — that depends on what the secret protects, and the honest answer differs per secret.

| Secret | Restart required? | Blast radius of rotating it |
|---|---|---|
| `ANTHROPIC_API_KEY`, `GOOGLE_GEMINI_KEY`, `ALPHA_VANTAGE_KEY` | **No** — `reload_secrets()` is enough | None. Read per call |
| `WEBHOOK_API_KEY` | **No** | In-flight automation callbacks using the old key are rejected |
| `MONGO_URL`, `REDIS_URL` | **Yes** | The client pool is built once at startup and holds the old credential |
| `JWT_SECRET` | No, but | **Every live session is invalidated** — all users are forced to re-login. This is a semantic consequence of changing a signing key, not a limitation of the loader |
| `CSRF_SECRET` | No, but | Outstanding CSRF tokens become invalid; in-flight form submissions fail once |
| `RECOVERY_SECRET` | No, but | Outstanding email-verification and password-reset links stop working |
| `BROKER_TOKEN_KEY` | **Yes**, and ⚠ | **Not safe in isolation.** Broker tokens already encrypted with the old key become undecryptable. Requires a re-encryption migration — which does not exist yet (§8, L4) |
| Google OAuth / broker API pairs | **Yes** | Provider-side rotation; connected users may need to re-authorize |

### Why in-place rotation works at all

A rotated Docker config or Kubernetes projected-volume secret updates the file *in place*, under the same mount path. Because nearly every consumer in this codebase reads `os.environ` at call time rather than capturing it at import, re-running the loader propagates the new value to live code:

```python
from security import secrets
changed = secrets.reload_secrets()
# {'JWT_SECRET': 'a60f178fea5e → bd960a4be1b7'}   ← fingerprints, never values
```

`reload_secrets()` is idempotent, reports only what actually changed, and — importantly — **drops a revoked secret**. If the file source disappears, the value the loader previously wrote into `os.environ` is removed rather than left behind. A deleted secret must stop working; leaving a stale write in place would mean a revocation silently did nothing.

**What is not built yet:** nothing *calls* `reload_secrets()` automatically. There is no file watcher and no admin endpoint — those need an authenticated trigger and an audit event, which is a sprint of its own. Today rotation is: update the file, then either call the function from a one-shot job or restart the container. See §8, L5.

---

## 7. Migrating beyond Compose

The loader was designed so that each of these is a *deployment* change, not a code change. None is implemented — they are out of scope for PH2.3 and recorded here so the next sprint does not have to re-derive them.

**Docker Swarm.** `docker-compose.secrets.yml` works unchanged. Create the secrets in the encrypted Raft store and swap the definitions to `external: true`:

```bash
docker secret create jwt_secret ./secrets/jwt_secret
```
```yaml
secrets:
  jwt_secret:
    external: true
```
This removes the one exposure Compose cannot: the secret is no longer plaintext on a host disk. It is mounted from tmpfs and never written to any node's filesystem.

**Kubernetes.** Mount a `Secret` as a volume at `/run/secrets` and the existing discovery finds every key with no manifest-side `_FILE` wiring:

```yaml
volumeMounts: [{ name: app-secrets, mountPath: /run/secrets, readOnly: true }]
volumes:      [{ name: app-secrets, secret: { secretName: stockassist } }]
```
Use *volumes*, not `envFrom` — `envFrom` reintroduces exactly the plaintext-environment exposure this sprint removed. Projected-volume secrets also update in place on rotation, which is what makes `reload_secrets()` useful there.

**Vault / AWS Secrets Manager / Azure Key Vault / GCP Secret Manager.** All four have an agent-injector or CSI-driver mode that writes secrets to a pod-local path. Point `SECRETS_DIR` at it:

```yaml
env: [{ name: SECRETS_DIR, value: /vault/secrets }]
```
No application change. Direct API integration (an SDK call at boot) would be a new source in `resolve_secret()` — a contained addition at one function, which is the reason the resolution order is a single function rather than scattered logic.

---

## 8. Known limitations

**L1 — After hydration, secrets are in the process environment.** By design (§2). `docker inspect` and host-side container metadata no longer expose them; `/proc/<pid>/environ` inside the container still does. Removing that too would mean routing all ~30 consumers through a lazy accessor — a large, risky refactor of security-critical code for a strictly smaller threat model (it only helps against an attacker who already has code execution in the container, who can also read `/run/secrets`).

**L2 — MongoDB's app-user credentials are still plaintext environment variables.** `docker/mongodb/init-app-user.js` runs under `mongosh` and can only reach `process.env`; it has no filesystem access to read a mounted secret. Reworking it means generating the init script at runtime from a shell wrapper, trading a documented exposure for a fragile one. The exposure is bounded: `MONGO_APP_USERNAME`/`MONGO_APP_PASSWORD` are consumed only on **first** initialization of an empty volume, and the account they create holds `readWrite` on one database and nothing else. The *root* password — the credential that can drop every database — is fully file-backed.

**L3 — Redis's password is partially exposed.** The official redis image has no `_FILE` support and cannot read `requirepass` from a separate file. The overlay's `sh -c` wrapper removes it from `docker inspect`'s `Cmd`, but the expanded value is in `redis-server`'s argv, visible in `ps` *inside that container*. Separately, `REDISCLI_AUTH` must remain an environment variable because the healthcheck runs as a bare exec, not through a shell — so `docker inspect stockassist-redis` still shows the password. Closing both requires generating a `redis.conf` at startup.

**L4 — Rotating `BROKER_TOKEN_KEY` is destructive.** Broker tokens already encrypted with the old key become undecryptable; there is no re-encryption migration. Affected users must reconnect their broker accounts. A rotation runner that decrypts-with-old / re-encrypts-with-new belongs to the sprint that owns broker persistence.

**L5 — No automatic reload trigger.** `reload_secrets()` exists and is tested, but nothing calls it on a schedule, on a file-watch event, or from an endpoint. An admin-triggered reload needs authentication and an audit event; a file watcher needs a debounce and a failure policy. Until then, rotation means restart (or a one-shot job).

**L6 — `./secrets/` is plaintext on the host disk.** Inherent to Compose's file-based secrets, and the reason §7's Swarm path exists. Files are `chmod 600` and git-ignored; the directory uses a deny-by-default `.gitignore` so a secret added next month is ignored without anyone remembering to add a rule.

**L7 — No secret scanning of the secrets directory in CI.** `gitleaks` runs on the repository (PH1.9) and the `.gitignore` is deny-by-default, but nothing actively verifies that no secret file was force-added. A `git ls-files secrets/` assertion belongs in PH2.5's CI foundation.

---

## 9. Testing

`backend/tests/test_secret_loading.py` — **68 hermetic tests** covering the source layer, and `backend/tests/test_secrets.py` — 38 covering the configuration surface. Both run with no Mongo, no network, and no privileges.

File reads go through an injected `reader`, so precedence, conflicts, empty files and unreadable paths are exercised against a dict. The cases that are specifically *about* the filesystem use `tmp_path` and the real reader, because a fake one could not prove them:

| Sprint requirement | Tests |
|---|---|
| Environment variable loading | `test_env_var_is_the_fallback_source`, `test_absent_variable_resolves_to_absent_not_empty_string` |
| Docker Secret loading | `test_discovers_docker_secret_by_lowercase_name`, `…_by_exact_name`, `test_real_docker_style_mount_resolves` |
| `_FILE` convention | `test_file_ref_reads_the_pointed_at_file`, `…_strips_the_trailing_newline`, `…_works_for_an_unregistered_variable` |
| Precedence | `test_precedence_file_ref_beats_discovery_and_env`, `test_precedence_discovery_beats_plaintext_env`, `test_file_ref_pointing_into_the_secrets_dir_is_not_a_self_conflict` |
| Missing secret handling | `test_missing_file_ref_is_an_error`, **`test_missing_file_ref_never_silently_falls_back_to_the_env_var`**, `test_empty_file_ref_target_is_an_error` |
| Invalid secret rejection | `test_weak_secret_values_are_detected`, `test_weak_signing_secret_fails_production_boot`, `test_invalid_fernet_key_is_rejected_in_every_environment`, `test_real_mounted_directory_is_rejected`, `test_real_oversized_file_is_rejected`, `test_real_binary_file_is_rejected_with_actionable_advice` |
| Placeholder rejection | `test_placeholder_secret_still_fails_production_boot`, `test_placeholder_arriving_from_a_file_is_rejected_too` |
| Production boot failure | `test_production_boot_fails_on_an_unreadable_secret_file`, `test_production_mongo_url_without_credentials_is_rejected`, `test_production_redis_without_a_password_is_rejected`, `test_require_file_secrets_turns_plaintext_into_a_boot_failure` |
| Development fallback | `test_development_is_not_nagged_about_plaintext_secrets`, `test_weak_secret_is_only_a_warning_in_development`, `test_development_mongo_url_without_credentials_is_only_a_warning` |
| Rotation | `test_reload_detects_a_rotated_secret_by_fingerprint`, `test_reload_drops_a_revoked_secret_from_the_environment`, `test_load_secrets_is_idempotent` |
| No secret ever leaks | `test_source_errors_never_contain_the_secret_value`, `test_conflict_error_names_the_sources_but_never_the_values`, `test_resolved_secret_repr_does_not_expose_the_value`, `test_resolution_summary_line_is_value_free` |

```bash
cd backend && ./venv/bin/python -m pytest tests/test_secret_loading.py tests/test_secrets.py -q
# 106 passed
```

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `JWT_SECRET_FILE → secret file not found: /run/secrets/jwt_secret` | Secret not mounted, or the name in `secrets:` does not match | `docker compose exec backend ls -l /run/secrets`; check both the service-level and top-level `secrets:` entries |
| `X is supplied by more than one source` | A file source and a plaintext value both exist | Delete one. Usually: retract the env value with `X: ""` in the overlay, or remove it from `production.env` |
| `secret path is not a regular file (mounted a directory?)` | A `volumes:` bind created a directory where a file was expected | Mount via `secrets:`, not `volumes:`; remove the accidentally-created host directory |
| `cannot read secret file …: Permission denied` | Mounted `0400 root:root`; the backend runs as uid 10001 | Use Docker `secrets:` (mode 0444) rather than a hand-rolled bind mount |
| `secret file … is not valid UTF-8` | A raw binary key was mounted | Base64-encode it before mounting |
| Signature verification fails but the secret "looks right" | A trailing newline in a value the loader did not read — e.g. one delivered by a tool outside this path | The loader strips whitespace; check whether the value is reaching it through a source it owns |
| Boot refused, message mentions low entropy | A hand-typed secret | `./secrets/generate.sh --rotate`, or `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `MONGO_URL carries no username:password` in production | Credential-free URI | Use the composed URI from `./secrets/generate.sh`, or add `user:pass@` |
| Everything works, but `docker inspect` still shows a secret | That variable is still plaintext | `docker compose logs backend \| grep plaintext` lists which; migrate it to a file (§5) |

---

## 11. Related documentation

- [`.claude/SECRETS.md`](../../.claude/SECRETS.md) — the secret **inventory**: every variable, sensitivity, rotation policy, supply-chain policy, incident runbook
- [DOCKER_COMPOSE.md](DOCKER_COMPOSE.md) — the stack these secrets are delivered into (PH2.2)
- [DOCKER.md](DOCKER.md) — the backend image and its entrypoint (PH2.1)
- [`secrets/README.md`](../../secrets/README.md) — the host-side secret files and the generator
- `backend/security/secrets.py` — the implementation, and the authority if this document disagrees with it
- `.claude/SECURITY_ARCHITECTURE.md` §23–§24 — where secret management sits in the security architecture

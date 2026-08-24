# StockAssist AI — Secrets & Supply-Chain Runbook
Owner: Security Engineering · Introduced: PH1.9 (2026-07-22) · Extended: PH2.3 (2026-07-22)

> **This document is the secret *inventory* and *runbook* — what each secret is,
> who owns it, when it rotates, and what to do when one leaks.
> [`docs/deployment/SECRETS.md`](../docs/deployment/SECRETS.md) is the secret
> *delivery mechanism* — Docker Secrets, the `_FILE` convention, the loader's
> precedence order, and the container-side workflows. Read that one when you are
> deploying; read this one when you are operating.**

This is the authoritative operational document for **secret management** and
**software supply-chain security**. It complements SECURITY.md (policy) and
SECURITY_ARCHITECTURE.md (design) with the concrete lifecycle, rotation, and
incident procedures an operator follows.

The **code** counterpart is `backend/security/secrets.py` — the single source of
truth for the configuration surface. This document and `backend/.env.example`
are generated to match that registry; if they disagree, the registry wins.

---

## 1. Principles

1. **Secrets live only in the environment — and preferably not even there.**
   Never in source, never in git, never in a log, never in a client bundle.
   Loaded from `backend/.env` locally (git-ignored) and, in staging/production,
   from a **file-backed source**: a Docker/Swarm/Kubernetes secret, or a
   `<NAME>_FILE` pointer (PH2.3). Plaintext environment variables remain
   supported — they are the development path and the migration fallback — but a
   sensitive value delivered that way is reported at every production boot,
   because it is visible in `docker inspect` and inherited by every child
   process.
2. **Fail closed.** The process refuses to start when a critical secret is
   missing or weak. `security.secrets.validate_config()` runs at boot, before
   the database client or any route. See §5.
3. **One registry.** Every variable the app reads is declared in
   `SECRET_REGISTRY` with its category, sensitivity, and which environments
   require it. Adding a `os.environ[...]` read without a registry entry is a
   review defect.
4. **Least privilege & separation.** Development, staging, and production use
   **distinct** credentials and distinct API keys. A dev key must never be able
   to touch production data, and vice-versa.
5. **Rotatable by design.** Signing keys (`JWT_SECRET`, `CSRF_SECRET`,
   `RECOVERY_SECRET`) can be rotated; rotation invalidates the corresponding
   live tokens (a feature, not a bug — see §6).

---

## 2. Environment strategy

| Environment | `APP_ENV` | Secret source | Cookie `Secure` | Validation severity |
|-------------|-----------|---------------|-----------------|---------------------|
| Development | `development` (default) | `backend/.env` (git-ignored) | optional (`COOKIE_SECURE`) | lenient — only the core trio is fatal; weak/placeholder optionals warn |
| Staging     | `staging` | Docker secrets / platform secret store | forced by deploy | strict for required set; mirrors production |
| Production  | `production` | Docker/Swarm/K8s secrets, or a vault-injected `SECRETS_DIR` | **forced true** | strict — missing required, short or low-entropy signing keys, placeholder values, credential-free `MONGO_URL`, and passwordless `REDIS_URL` are all fatal |

`APP_ENV` selects severity. An unrecognized value aborts startup rather than
silently defaulting. Environment detection has exactly one definition
(`security.cookies.is_production`), reused by cookies, CORS, and secret
validation so they can never disagree.

**Production never silently degrades.** There is no code path in which a missing
production secret resolves to a default, and none in which an unreadable secret
file falls back to a plaintext environment variable — that downgrade is exactly
how a rotation appears to succeed while the old value stays in use. Both are hard
boot failures.

### Delivery control variables (PH2.3)

| Variable | Default | Effect |
|---|---|---|
| `SECRETS_DIR` | `/run/secrets` | Directory scanned for file-backed secrets. Point it at a secret-manager sidecar's output path (e.g. `/vault/secrets`) to adopt one with no code change |
| `REQUIRE_FILE_SECRETS` | `false` | When true, a *sensitive* secret arriving as a plaintext env var is a boot **error** rather than a warning. Turn it on once a deployment has finished migrating, so the posture cannot silently regress |

---

## 3. Secrets inventory

Categories and per-variable metadata are defined in `SECRET_REGISTRY`
(`backend/security/secrets.py`) and rendered into `backend/.env.example`. The
inventory below is the human summary; the registry is authoritative.

| Secret | Category | Sensitive | Required in | Purpose | Rotation |
|--------|----------|-----------|-------------|---------|----------|
| `MONGO_URL` | core | yes | all | Mongo connection (may embed creds) | Quarterly / on exposure |
| `DB_NAME` | core | no | all | Database name | N/A |
| `JWT_SECRET` | auth-signing | yes | all (min 32) | JWT signing; fallback HMAC for CSRF/recovery | Quarterly; invalidates sessions |
| `FRONTEND_URL` | app-config | no | staging, prod | SPA origin for redirects/links | N/A |
| `CORS_ALLOWED_ORIGINS` | app-config | no | prod | Exact-match CORS allowlist (never `*`) | N/A |
| `SECRETS_DIR` | app-config | no | — | Directory scanned for file-backed secrets (PH2.3); default `/run/secrets` | N/A |
| `REQUIRE_FILE_SECRETS` | app-config | no | — | Make plaintext delivery of a sensitive secret a boot error (PH2.3) | N/A |
| `CSRF_SECRET` | auth-signing | yes | optional (min 32) | Dedicated CSRF HMAC key | Quarterly; invalidates CSRF tokens |
| `RECOVERY_SECRET` | auth-signing | yes | optional (min 32) | Dedicated recovery-token HMAC key | Quarterly; invalidates recovery links |
| `ANTHROPIC_API_KEY` | ai-provider | yes | ≥1 AI in prod | Claude API | 90 days / on exposure |
| `GOOGLE_GEMINI_KEY` | ai-provider | yes | ≥1 AI in prod | Gemini API | 90 days / on exposure |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | oauth | secret on the secret | both-or-neither | Google sign-in | On exposure |
| `ALPHA_VANTAGE_KEY` | market-data | yes | optional | Intraday fallback source | On exposure |
| `KITE_API_KEY` / `KITE_API_SECRET` | broker | yes | both-or-neither | Zerodha Kite | On exposure |
| `UPSTOX_API_KEY` / `UPSTOX_API_SECRET` | broker | yes | both-or-neither | Upstox | On exposure |
| `ANGELONE_API_KEY` / `ANGELONE_REDIRECT_URL` | broker | key only | key + redirect | Angel One SmartAPI — no app secret: the publisher login returns the session tokens on the redirect, so there is nothing server-side to sign | On exposure |
| `BROKER_TOKEN_KEY` | broker | yes | optional | Fernet key for broker tokens at rest (derived from `JWT_SECRET` when unset) | Careful — re-encrypts stored tokens |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | notifications | yes | optional | WhatsApp alerts | On exposure |
| `SENDGRID_API_KEY` / `SMTP_USER` / `SMTP_PASSWORD` | notifications | yes | optional | Transactional email | On exposure |
| `WEBHOOK_API_KEY` | notifications | yes | optional (recommended prod) | Inbound n8n webhook shared secret | On integration rotation |
| `TELEGRAM_BOT_TOKEN` | notifications | yes | optional | Telegram alert channel | On exposure |
| `REDIS_URL` | infrastructure | yes | optional | Cross-process realtime fan-out | On exposure |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | admin-bootstrap | pw is secret | dev only | `scripts/seed_dev_admin.py` (refuses in prod) | N/A (dev only) |
| `ENABLE_AUTO_LOGIN` | admin-bootstrap | no | dev only | Must be false/unset in prod | N/A |

Non-secret configuration variables (`APP_ENV`, `COOKIE_*`, `SMTP_HOST/PORT`,
`EMAIL_FROM*`, `*_REDIRECT_URL`, phone numbers) are declared in the registry for
completeness but are not secrets.

---

## 4. Adding or changing a configuration variable

1. Add/edit the `SecretSpec` in `SECRET_REGISTRY` (`backend/security/secrets.py`)
   — set `category`, `sensitive`, `required_in`, `description`, `example`, and
   `min_length` for signing keys.
2. Regenerate the template: `python scripts/generate_env_example.py`.
3. Read the value **only** via `security.secrets.get()` / `require()` (or, for
   existing modules, `os.environ` guarded by the boot validator). Either is safe
   for a file-backed secret: `load_secrets()` has already hydrated `os.environ`
   before any module is imported.
4. Update this document's inventory row.
5. CI (`config-sync` job) enforces that the template matches the registry.

**Registry membership is what makes a variable discoverable as a Docker secret.**
Auto-discovery from `$SECRETS_DIR` is limited to registered names — deliberately,
so a stray file in the secrets mount cannot invent an environment variable. A
variable added to the code but not to the registry therefore silently loses file
support, on top of losing validation. (The `<NAME>_FILE` pointer form works for
any name, registered or not, as an escape hatch.)

---

## 5. Boot-time loading & validation (fail-fast)

`server.py` calls `security.secrets.validate_config()` immediately after
`load_dotenv` and **before any other application module is imported**. That call
does two things in order:

**1. Load (PH2.3).** `load_secrets()` resolves every variable from its
highest-precedence source — `<NAME>_FILE` pointer, then `$SECRETS_DIR/<name>`,
then a plaintext environment variable — and materializes the result into
`os.environ`. Hydrating before the other modules import is what lets ~30
existing `os.environ` consumers read a Docker secret without a call-site change.
Precedence, conflict handling and the file-source failure modes are documented in
[`docs/deployment/SECRETS.md`](../docs/deployment/SECRETS.md) §3.

**2. Validate.** It then:

- collects **all** problems — source failures and value failures alike — into one
  aggregated, value-free error, so an operator fixes the whole environment in one
  pass rather than one variable per crash-loop,
- treats the core trio (`MONGO_URL`, `DB_NAME`, `JWT_SECRET`) as fatal in every
  environment,
- in production, additionally makes fatal: any missing required secret, a
  signing key shorter than 32 chars, any placeholder/weak value, a **low-entropy**
  sensitive value (PH2.3 — `aaaa…` clears a length check but not an offline
  attack), a `MONGO_URL` with no credentials, a `REDIS_URL` with no password, a
  half-configured OAuth or broker pair, `ENABLE_AUTO_LOGIN=true`, a weak
  `ADMIN_PASSWORD`, and the absence of any AI provider,
- makes fatal **in every environment** an invalid `BROKER_TOKEN_KEY` (PH2.3 — a
  malformed Fernet key does not fail at boot, it fails the first time a user
  connects a broker, weeks later),
- logs a **presence-only** summary (variable names, sources, and counts — never
  values), including how many secrets are still delivered as plaintext.

`docker/entrypoint.sh` runs the same validation as a throwaway dry run before
starting uvicorn, so the operator sees the clean aggregated report instead of a
traceback from inside the server. The resolved values do not cross that process
boundary.

Verify locally: set `APP_ENV=production` with an incomplete `.env` and start the
server — it must abort with the aggregated error and touch nothing.

---

## 6. Rotation policy

| Class | Cadence | Procedure | Blast radius |
|-------|---------|-----------|--------------|
| Signing keys (`JWT_SECRET`, `CSRF_SECRET`, `RECOVERY_SECRET`) | Quarterly + on suspected exposure | Generate `python -c "import secrets;print(secrets.token_urlsafe(48))"`, update secret store, restart | Rotating `JWT_SECRET` invalidates all sessions (forces re-login) and, unless dedicated keys are set, CSRF + recovery tokens. Set `CSRF_SECRET`/`RECOVERY_SECRET` in prod so their rotation is independent. |
| Provider API keys (Anthropic, Gemini, Alpha Vantage, Twilio, SendGrid) | 90 days + on exposure | Create new key in provider console → update store → restart → revoke old | Brief dual-validity window; no user impact |
| OAuth client secret (Google) | On exposure / annually | Rotate in Google Cloud console → update store | Existing sessions unaffected; new logins use new secret |
| Broker keys (Kite, Upstox) | On exposure | Rotate in broker developer console → update store | Users must re-link brokers if the secret is invalidated |
| `BROKER_TOKEN_KEY` (Fernet) | Rare / on exposure | Rotating changes the at-rest encryption key; stored broker tokens must be re-encrypted or users re-link | Stored broker tokens |
| Database / Redis credentials | Quarterly + on exposure | Rotate in the managed DB console → update `MONGO_URL`/`REDIS_URL` → restart | Connection blip on restart |

General rule: prefer **overlap** (new key valid before old is revoked) to avoid
downtime; restart the app after updating the store so the new value is read.

**Which rotations actually need a restart (PH2.3).** File-backed secrets update
in place — a rotated Docker config or Kubernetes projected volume rewrites the
same path — and nearly every consumer in this codebase reads `os.environ` at call
time rather than capturing it at import. So `security.secrets.reload_secrets()`
propagates a new value to live code and reports what changed *by fingerprint*,
never by value. It also **drops a revoked secret** from the environment: if the
file disappears, the previously-loaded value is removed rather than left working.

Provider API keys and `WEBHOOK_API_KEY` therefore rotate without a restart.
`MONGO_URL` and `REDIS_URL` still need one — their client pools are built once at
startup. `JWT_SECRET`, `CSRF_SECRET` and `RECOVERY_SECRET` do not need a restart,
but rotating them invalidates the corresponding live tokens regardless of how the
new value arrived — that is a property of changing a signing key, not of the
loader. Per-secret blast radius: `docs/deployment/SECRETS.md` §6.

⚠ Nothing calls `reload_secrets()` automatically yet — no watcher, no endpoint.
Until that exists (SECRETS.md §8, L5), rotation in practice means restart.

---

## 7. Dependency & supply-chain policy

- **Pinning.** `backend/requirements.txt` (runtime) and
  `backend/requirements-dev.txt` (dev/CI) are **fully exact-pinned** (`==`). No
  floating lower bounds — reproducible builds and a stable audit surface.
  Frontend uses a committed lockfile.
- **Runtime vs. dev split (PH1.11 / M14).** `requirements.txt` contains only
  what the server imports at runtime. Developer/CI-only tooling — `pytest`,
  `black`, `flake8`, `isort`, `mypy` and their exclusive transitive deps — lives
  in `requirements-dev.txt`, which begins with `-r requirements.txt`. Rules:
  - Install for dev/CI: `pip install -r requirements-dev.txt`.
  - Install for the production image: `pip install -r requirements.txt` **only**
    (PH2.1's Dockerfile installs the runtime set — a smaller runtime is a smaller
    attack surface; dev tooling never ships).
  - A package belongs in `requirements-dev.txt` iff, per `pip show <pkg>`, it is
    `Required-by` *only* dev tools. If a runtime package later needs it, move it
    back — CI's `pip install -r requirements.txt && pip check` fails otherwise.
- **Auditing.** The `security-audit` GitHub Actions workflow runs on every
  push/PR and weekly:
  - `pip-audit --strict` against **both** pinned requirements files,
  - `pip check` on the **runtime-only** install (also proves the M14 split),
  - `npm audit --audit-level=high` for the frontend,
  - `gitleaks` history scan + a guard that no `.env` is ever tracked.
  Run the backend checks locally with `python scripts/audit_dependencies.py`.
- **Automated updates (Dependabot).** `.github/dependabot.yml` opens weekly
  (Monday 06:00 UTC) update PRs for `pip` (`/backend`), `npm` (`/frontend`), and
  `github-actions`. The `docker` ecosystem is staged (commented) and activates
  with PH2.1/PH2.2 when the Dockerfiles land. Non-security minor/patch bumps are
  grouped into one PR per ecosystem; **security** updates always arrive as their
  own PRs. Every PR must pass the full CI suite before merge.
- **Triage SLA (by advisory severity).** From when an advisory is surfaced (by
  `pip-audit`/`npm audit`/Dependabot) to a merged fix or a recorded acceptance:

  | Severity | SLA | Action if unfixable in time |
  |----------|-----|-----------------------------|
  | Critical | Immediate — **blocks merge/release** | Patch, pin around, or remove the dependency; no exceptions to shipping a known-critical to prod |
  | High | 7 days | Record an accepted-risk entry in §8 with justification + revisit date |
  | Medium | 30 days | §8 backlog entry |
  | Low | 90 days | §8 backlog entry |

  The same table is mirrored in `.claude/TESTING.md` (QA gate) and referenced by
  `.github/dependabot.yml`.
- **Updating.** Bump one package (or a coherent group) at a time; run the test
  suite; commit with the advisory id in the message.
- **Accepting an advisory you cannot fix (PH3.11 onward).** Add an entry to
  **`.github/dependency-triage.yml`** — not a flag in the workflow, which is how
  the previous mechanism rotted. The entry must carry `package`, `severity`,
  `classification` (`not-reachable` or `temporarily-accepted`), `reason`,
  `reachability`, `mitigation`, `owner` and `expires`; a `not-reachable` entry
  must additionally carry `evidence`, a **command a reviewer can re-run**, and
  the gate rejects the register outright if it does not. Verify with
  `python .github/scripts/dependency_audit.py --ecosystem all` before pushing. Both
  ecosystems go through the same register — npm included, which it previously
  was not. Then mirror the entry in §8 for human readers.
- **New dependencies.** Justify in the PR (why, maintenance health, transitive
  weight). Prefer the standard library. Every add must keep `pip check` clean and
  land in the correct file (runtime vs. dev).

---

## 8. Known/accepted advisories (remediation backlog)

> **The authoritative, enforced list is `.github/dependency-triage.yml`.** This
> section is the human-readable summary; the register is what CI reads, and
> `.github/scripts/dependency_audit.py` fails the build on any advisory not in it, any
> entry past its expiry, and any entry that no longer matches a real finding.
> If the two ever disagree, the register is right and this section is stale.

**Updated 2026-08-17 (PH3.11 remediation).** The previous version of this table
was wrong in a way worth recording: it listed `litellm` and `ecdsa` as tracked
backlog **after both packages had already been removed from
`requirements.txt`**. Eight of the fifteen suppressions in CI matched nothing.
Nothing checked, so nothing noticed — which is why the register now fails the
build on a stale entry.

**Fixed in PH3.11**, rather than deferred:

| Package | Was → Now | Cleared |
|---------|-----------|---------|
| `aiohttp` | 3.14.1 → **3.14.3** | PYSEC-2026-3545/3546/3547 |
| `cryptography` | 48.0.1 → **50.0.0** | PYSEC-2026-3552/3553/3554 |
| npm `brace-expansion`, `fast-uri`, `js-yaml`, `nanoid`, `underscore` | patch-level `overrides` | 7 packages |
| npm `postcss` (direct devDependency) | ^8.4.49 → **^8.5.26** | 5 GHSAs |

The `cryptography` upgrade crosses two majors. It was taken because the analysis
supported it, not because the advisories forced it — all three were already
unreachable. No dependent caps the version (every constraint is a lower bound),
the application's entire surface is one `from cryptography.fernet import Fernet`,
and **a Fernet token written under 48.0.0 decrypts correctly under 50.0.0**,
which is the property that matters for broker tokens already sitting in a
production database.

**Still accepted, all with enforced expiry dates:**

| Package | Advisories | Classification | Why | Expires |
|---------|-----------|----------------|-----|---------|
| `starlette` 0.37.2 | PYSEC-2026-249/1941/1943/2280/2281 | not-reachable | No form or multipart parsing anywhere in the backend; no `HTTPEndpoint`; no `StaticFiles`; 2281 is Windows-only and the image is Linux | **2027-02-15** |
| `starlette` 0.37.2 | PYSEC-2026-161/248 | temporarily-accepted | The app reads only `request.url.path`, never the reconstructed absolute URL — unreachable **by convention, not by control**. Mitigation: Host validation at the proxy (C-7); `TrustedHostMiddleware` is the recommended follow-up | **2026-11-15** |
| npm CRA build chain (11 packages, 16 rows) | see register | not-reachable | Build tooling only: zero imports from `frontend/src/`, every dependency path runs through `react-scripts`, and none appears in the shipped bundle | **2026-11-15** |

All seven `starlette` advisories are pinned in place by `fastapi==0.110.1`
(`starlette<0.38`); every fix is 0.40.0 or later, so clearing them means a
coordinated FastAPI + Starlette upgrade in its own sprint. The npm group clears
only by migrating off Create React App — npm reports the fix for each as
`react-scripts@0.0.0`, i.e. "remove it".

**On changing an expiry date.** The previous mechanism used a single
`SUPPRESSION_REVIEW_BY` covering everything, which made "re-argue the case" and
"move one date" indistinguishable. PH3.11 replaced it with per-entry dates and
re-argued the old one rather than extending it: the blanket "pinned by fastapi"
justification split 5/2, and the two advisories that could not be dismissed
received a **shorter** deadline than they had before. Editing `expires` without
updating `reason` and `reachability` in the same change is the failure this
register exists to prevent.

---

## 9. Incident response — leaked credential

1. **Contain (minutes).** Revoke/rotate the exposed secret at the source
   (provider console, DB console, or generate a new signing key). Deploy the new
   value. The old credential must stop working.
2. **Assess.** Determine exposure window and blast radius using the inventory
   (§3). For `JWT_SECRET`: assume all outstanding tokens are forgeable — rotating
   it invalidates them. For a provider key: check provider usage logs for abuse.
3. **Purge from history (if committed).** If a secret reached git, rotation is
   mandatory **and** history must be rewritten (`git filter-repo`) and force-
   pushed; treat the value as permanently compromised regardless.
4. **Verify.** Confirm `gitleaks` is clean and no `.env` is tracked
   (`git ls-files | grep -E '\.env'` returns nothing but `.env.example`).
5. **Record.** Log the incident (what, when, window, action) in the security
   audit trail and note any advisory acceptance in §8.

**Standing notes (PH1.9), from the audit on 2026-07-22:**

- The live provider values in local `backend/.env` / `.env` (Google OAuth
  secret, Gemini/Alpha Vantage/Kite/Upstox/Twilio keys, `JWT_SECRET`) are **not
  present in git history** — verified with `git log --all -S <value>` (0 commits
  each) and no `.env` was ever tracked. They have, however, existed in plaintext
  developer files: **rotate them before production launch** and load production
  values only from the platform secret store.
- One committed secret **was** found: the n8n editor password
  `alphapartner123`, hard-coded in the tracked `docker-compose.yml` (present in
  5 historical commits). PH1.9 externalized it to a required
  `N8N_BASIC_AUTH_PASSWORD` env var (no baked default). Severity is low — it
  gated only the **local** n8n basic-auth editor UI in the dev compose stack,
  never a production or user-facing credential. Treat the value as compromised:
  do not reuse it anywhere. A history rewrite is optional given it is a
  local-only demo password; if the compose stack is ever exposed beyond
  localhost, rewrite history (`git filter-repo`) as well.

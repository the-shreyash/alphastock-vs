# StockAssist AI — Secrets & Supply-Chain Runbook
Owner: Security Engineering · Introduced: PH1.9 (2026-07-22)

This is the authoritative operational document for **secret management** and
**software supply-chain security**. It complements SECURITY.md (policy) and
SECURITY_ARCHITECTURE.md (design) with the concrete lifecycle, rotation, and
incident procedures an operator follows.

The **code** counterpart is `backend/security/secrets.py` — the single source of
truth for the configuration surface. This document and `backend/.env.example`
are generated to match that registry; if they disagree, the registry wins.

---

## 1. Principles

1. **Secrets live only in the environment.** Never in source, never in git,
   never in a log, never in a client bundle. Loaded from `backend/.env` locally
   (git-ignored) and from the platform secret store in staging/production.
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
| Staging     | `staging` | platform secret store | forced by deploy | strict for required set; mirrors production |
| Production  | `production` | platform secret store / vault | **forced true** | strict — missing required, short signing keys, and placeholder values are fatal |

`APP_ENV` selects severity. An unrecognized value aborts startup rather than
silently defaulting. Environment detection has exactly one definition
(`security.cookies.is_production`), reused by cookies, CORS, and secret
validation so they can never disagree.

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
| `CSRF_SECRET` | auth-signing | yes | optional (min 32) | Dedicated CSRF HMAC key | Quarterly; invalidates CSRF tokens |
| `RECOVERY_SECRET` | auth-signing | yes | optional (min 32) | Dedicated recovery-token HMAC key | Quarterly; invalidates recovery links |
| `ANTHROPIC_API_KEY` | ai-provider | yes | ≥1 AI in prod | Claude API | 90 days / on exposure |
| `GOOGLE_GEMINI_KEY` | ai-provider | yes | ≥1 AI in prod | Gemini API | 90 days / on exposure |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | oauth | secret on the secret | both-or-neither | Google sign-in | On exposure |
| `ALPHA_VANTAGE_KEY` | market-data | yes | optional | Intraday fallback source | On exposure |
| `KITE_API_KEY` / `KITE_API_SECRET` | broker | yes | both-or-neither | Zerodha Kite | On exposure |
| `UPSTOX_API_KEY` / `UPSTOX_API_SECRET` | broker | yes | both-or-neither | Upstox | On exposure |
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
   existing modules, `os.environ` guarded by the boot validator).
4. Update this document's inventory row.
5. CI (`config-sync` job) enforces that the template matches the registry.

---

## 5. Boot-time validation (fail-fast)

`server.py` calls `security.secrets.validate_config()` immediately after
`load_dotenv`, before the Mongo client. It:

- collects **all** problems into one aggregated, value-free error,
- treats the core trio (`MONGO_URL`, `DB_NAME`, `JWT_SECRET`) as fatal in every
  environment,
- in production, additionally makes fatal: any missing required secret, a
  signing key shorter than 32 chars, any placeholder/weak value, a half-
  configured OAuth or broker pair, `ENABLE_AUTO_LOGIN=true`, a weak
  `ADMIN_PASSWORD`, and the absence of any AI provider,
- logs a **presence-only** summary (variable names + counts, never values).

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
  suite; commit with the advisory id in the message. To temporarily accept an
  unfixable advisory, add `--ignore-vuln <ID>` in the workflow **with a
  justification recorded in §8**.
- **New dependencies.** Justify in the PR (why, maintenance health, transitive
  weight). Prefer the standard library. Every add must keep `pip check` clean and
  land in the correct file (runtime vs. dev).

---

## 8. Known/accepted advisories (remediation backlog)

Snapshot from the PH1.9 audit (2026-07-22). Safe in-pin security patches were
applied (aiohttp, cryptography, httplib2, pillow, pyasn1, pymongo,
python-multipart). The following remain and are tracked:

| Package | Advisories | Why deferred | Plan |
|---------|-----------|--------------|------|
| `starlette` 0.37.2 | PYSEC-2026-161/248/249/1941/1943/2280/2281 | Hard-pinned by `fastapi==0.110.1` (`starlette<0.38`); cannot bump in isolation | Coordinated FastAPI+Starlette upgrade in a dedicated, tested sprint |
| `litellm` 1.80.0 | PYSEC-2026-388/390/2597–2600, GHSA-69x8-hrgq-fjj8 | AI engine dependency; out of PH1.9 scope (AI models); fast-moving with breaking changes | AI-dependency upgrade sprint with regression coverage |
| `ecdsa` 0.19.2 | PYSEC-2026-1325 (Minerva timing) | No fixed release exists; transitive via `python-jose` | Accepted risk — our own tokens use `PyJWT` HS256, not ECDSA; revisit if a fix ships or `python-jose` is dropped |

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

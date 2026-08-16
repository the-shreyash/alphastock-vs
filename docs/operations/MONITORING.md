# Monitoring & Observability

**Status:** PH2.5 complete (2026-07-22) · extended by PH2.6, PH2.7, PH3.6 and PH3.7 (2026-08-15) — v1.2
**Audience:** SRE, DevOps, on-call engineers
**Code:** `backend/observability/`
**Related:** [OBSERVABILITY.md](../architecture/OBSERVABILITY.md) — the architecture, the rules, and the alert catalogue; read it before adding an instrument · [LOGGING.md](LOGGING.md) · [DOCKER.md](../deployment/DOCKER.md) · [DOCKER_COMPOSE.md](../deployment/DOCKER_COMPOSE.md) · [SECRETS.md](../deployment/SECRETS.md) · [GITHUB_ACTIONS.md](../deployment/GITHUB_ACTIONS.md) · [runbooks.md](runbooks.md)

---

## 1. What this gives you

Before PH2.5, the backend's entire operational surface was one unauthenticated
`/api` endpoint and a single `logging.basicConfig` line. You could tell that the
process was alive. You could not tell whether it could reach its database, how
fast it was serving, how many requests were failing, which build was running, or
which log lines belonged to the request a user was complaining about.

This sprint added five things:

| Capability | Answers |
|---|---|
| Three health probes | Restart it? Route to it? Has it booted? |
| Metrics | How much traffic, how slow, how many errors, how saturated? |
| Structured logs | What happened — as queryable fields, not prose |
| Request correlation | Which lines belong to *this* request? |
| Runtime diagnostics | What build is this, and since when? |

PH3.7 added a sixth: **subsystem instrumentation** — which part of the system is
failing, and how (MongoDB, Redis, WebSockets, background tasks, cron, external
providers, AI, the event bus, and the browser). See
[OBSERVABILITY.md](../architecture/OBSERVABILITY.md) for the architecture and
the rules that bind anything added to it.

Explicitly **not** in scope, and still open: Prometheus/Grafana servers, an
alert *delivery* channel, error tracking (Sentry), distributed tracing, log
shipping. See §11.

---

## 2. Architecture

```
Client Request
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ ObservabilityMiddleware            (outermost, runs 1st) │
│   • resolve/generate X-Request-ID → contextvar           │
│   • start timer, increment in-flight gauge               │
└─────────────────────────────────────────────────────────┘
      │
      ▼   Security Headers → CORS → Rate Limiter → CSRF
      ▼
┌─────────────────────────────────────────────────────────┐
│ Route handler / services / repositories                  │
│   every logger.*() call inherits request_id from the     │
│   contextvar — no call-site changes required             │
└─────────────────────────────────────────────────────────┘
      │
      ▼   (unwinding)
┌─────────────────────────────────────────────────────────┐
│ record metrics · emit ONE access log line ·              │
│ stamp X-Request-ID on the response · reset context       │
└─────────────────────────────────────────────────────────┘
      │
      ├──▶ Structured Logs   stdout (JSON in prod)   → PH2.6 files / PH2.10 collector
      ├──▶ Metrics           /api/metrics            → PH2.10 Prometheus
      ├──▶ Health Endpoints  /api/health/*           → orchestrator / LB
      └──▶ Diagnostics       /api/diagnostics        → humans
```

### Modules

| Module | Responsibility |
|---|---|
| `observability/context.py` | Request-ID generation, validation, `contextvars` binding. Zero internal dependencies. |
| `observability/logging.py` | JSON/text formatters, `configure_logging()`, scrubbing, access log |
| `observability/metrics.py` | Counter/Gauge/Histogram registry + Prometheus text exposition |
| `observability/health.py` | Probe registry, lifecycle state machine, built-in Mongo/Redis probes |
| `observability/runtime.py` | Version, build provenance, uptime, process facts |
| `observability/middleware.py` | The single ASGI seam that ties it together |
| `observability/errors.py` | The closed 13-class failure vocabulary every label and log field uses (PH3.7) |
| `observability/instruments.py` | The API call sites use: closed `subsystem`/`provider` vocabularies, trackers, `record_*` helpers (PH3.7) |
| `observability/mongo_monitor.py` | Driver-level MongoDB command + pool metrics via pymongo listeners (PH3.7) |
| `observability/log_streams.py`, `log_rotation.py` | Stream separation, rotation, retention (PH2.6) |
| `observability/routes.py` | The HTTP endpoints + the production access gate + client-error ingest |

---

## 3. Health endpoints

### The three probes are not interchangeable

This is the single most important operational fact in this document. Each probe
is consumed by a different system that takes a **different destructive action**
on failure.

| Probe | Path | Asked by | On failure | Touches dependencies? |
|---|---|---|---|---|
| Liveness | `/api/health/live` | kubelet, Docker `HEALTHCHECK` | **container is killed and restarted** | **Never** |
| Readiness | `/api/health/ready` | load balancer, service mesh | removed from the traffic pool, left running | Yes — Mongo, Redis, configuration |
| Startup | `/api/health/startup` | orchestrator, during boot only | keeps waiting; suppresses liveness | No |

**Never point a liveness probe at a dependency check.** If liveness depends on
MongoDB, a 60-second database blip makes every replica report unhealthy *at the
same instant*, the orchestrator restarts *all of them at once*, and the empty
caches plus reconnect storm of a fleet-wide cold start land on a database that
was already struggling. A recoverable dependency wobble becomes a total,
self-sustaining outage. This is the classic cascading-failure mistake and it is
why `/api/health/live` performs no I/O whatsoever.

**Never point a container `HEALTHCHECK` at readiness.** Readiness failing means
"take me out of rotation" — restarting on it destroys the instance that was
about to recover. `backend/docker/healthcheck.sh` rejects a `not_ready`/`starting`
payload loudly for exactly this reason.

### Endpoint reference

#### `GET /api/health/live` → 200 (always, if the process can respond)

```json
{"status":"ok","service":"stockassist-backend","lifecycle":"ready",
 "uptime_seconds":3617.42,"timestamp":"2026-07-22T09:41:07.123456+00:00"}
```

Answering at all proves the event loop is scheduling coroutines and the ASGI
stack can serialize a response — precisely the failures a restart fixes. A
deadlocked process never reaches here and the probe times out.

#### `GET /api/health/ready` → 200 ready · 503 not ready

```json
{"status":"ready","service":"stockassist-backend","lifecycle":"ready",
 "checks":[
   {"name":"mongodb","status":"pass","critical":true,"duration_ms":1.84},
   {"name":"redis","status":"skip","critical":false,"duration_ms":0.02,
    "detail":"not configured"},
   {"name":"configuration","status":"pass","critical":true,"duration_ms":0.01}],
 "timestamp":"..."}
```

The **`configuration` check (PH3.7)** is critical: an instance whose required
configuration does not validate must not receive traffic. It reads the verdict
`validate_config()` already produced at boot rather than re-validating — an
attribute access, so it adds nothing measurable to a poll that every replica
performs every few seconds. On failure its detail is a **count** of failing
names, never the names: an unauthenticated caller learning which secret a
deployment is missing is a reconnaissance gift, and the names are already in the
boot log.

**503 is a correct answer, not an error** — it is how an instance asks to be
taken out of rotation while it recovers or drains.

Check statuses: `pass` · `fail` · `skip` (dependency not configured).
Readiness fails when **any critical check fails**, or the process has not
finished starting, or it is draining. `reason` says which.

Registered checks (`backend/server.py`):

| Check | Critical | Probe | Skips when |
|---|---|---|---|
| `mongodb` | **yes** | `db.command("ping")` | — |
| `redis` | no | `PING` | `REDIS_URL` unset |
| `configuration` | **yes** | boot-time `validate_config()` verdict (PH3.7) | no report wired |

Redis is non-critical deliberately: `services/cache.py` falls back to an
in-process dict, so the app keeps serving without it. Promote it to critical
once Redis becomes load-bearing (multi-process pub/sub fan-out).

#### `GET /api/health/startup` → 200 started · 503 starting

Boot builds ~20 Mongo indexes, restores broker sessions, initialises the market
gateway and starts four background loops. Until `lifecycle.mark_started()` runs
as the **last** statement of the startup handler, this returns 503. Without a
startup probe an aggressive liveness timer kills the container mid-boot, forever
— a crash loop whose logs stop in a different place each time.

#### `GET /api/health` → aggregate

Everything readiness returns, plus `uptime_seconds` and `started_at`. For humans.
**Do not point infrastructure at it** — use the specific probe.

### Lifecycle state machine

```
   starting ──mark_started()──▶ ready ──mark_stopping()──▶ stopping
   (startup 503)                (serving)                  (draining)
```

`stopping` is set as the **first** action of the shutdown handler, before
anything is torn down. The load balancer sees 503 on its next poll and stops
routing here while in-flight requests drain into a process that still works.
Without it, every release sheds a small burst of 500s.

### Orchestrator configuration

Kubernetes:

```yaml
startupProbe:                       # tolerate a slow boot
  httpGet: {path: /api/health/startup, port: 8000}
  failureThreshold: 30
  periodSeconds: 2                  # → up to 60s of boot budget
livenessProbe:
  httpGet: {path: /api/health/live, port: 8000}
  periodSeconds: 10
  failureThreshold: 3
readinessProbe:
  httpGet: {path: /api/health/ready, port: 8000}
  periodSeconds: 5
  failureThreshold: 2
```

Docker Compose / `HEALTHCHECK`: keep the existing `/api` target (PH2.1 contract,
asserted by CI). `docker/healthcheck.sh` also accepts `/api/health/live` — set
`HEALTHCHECK_PATH=/api/health/live` for lifecycle and uptime in
`docker inspect .State.Health.Log`.

---

## 4. Metrics

`GET /api/metrics` — Prometheus text exposition format (v0.0.4).
`GET /api/metrics?format=json` — the same data, readable during an incident.

No Prometheus client library and no Prometheus server. The **format** is adopted
because it is free to emit and universally consumed; PH2.10 only has to add a
scrape config.

### Exposed metrics

| Metric | Type | Labels | Signal |
|---|---|---|---|
| `http_requests_total` | counter | method, route, status | Traffic |
| `http_request_duration_seconds` | histogram | method, route | Latency |
| `http_request_errors_total` | counter | method, route, kind | Errors |
| `http_requests_in_flight` | gauge | — | Saturation |
| `app_uptime_seconds` | gauge | — | Crash-loop detection |
| `app_start_time_seconds` | gauge | — | Process start (unix) |
| `app_info` | gauge (=1) | version, environment, python_version, revision | Build metadata |
| `dependency_up` | gauge | dependency | 1 up · 0 down · **-1 not configured** |
| `health_check_duration_seconds` | histogram | dependency | Probe latency |
| `process_resident_memory_bytes` | gauge | — | Memory |
| `process_open_fds` | gauge | — | FD leaks |
| `metrics_series_dropped_total` | gauge | — | **Instrumentation bug detector** |
| `log_records_dropped_total` | gauge | — | Log files on this instance are incomplete (PH2.6) |

`kind` is `client` (4xx), `server` (5xx) or `exception` (handler raised without
producing a response) — three different pages at 3am.

`dependency_up = -1` is not the same as `0`: "never configured" and "down" need
different alert rules.

#### Redis (PH2.7)

Two families, split by where the number comes from — see
[`docs/infrastructure/REDIS.md`](../infrastructure/REDIS.md) §7 for the full
treatment and the suggested alert rules.

| Metric | Type | Labels | Signal |
|---|---|---|---|
| `redis_up` | gauge | — | 0 with `REDIS_URL` set = running on the in-process fallback |
| `redis_circuit_state` | gauge | — | 0 closed · 1 half-open · 2 open |
| `redis_pool_connections` | gauge | state | `in_use` near `max` = commands queueing |
| `redis_commands_total` | counter | operation, outcome | `outcome=unavailable` = never sent |
| `redis_command_duration_seconds` | histogram | operation | Buckets start at 100 µs, not 5 ms |
| `redis_connection_errors_total` | counter | — | Link failures only, not command errors |
| `redis_pubsub_reconnects_total` | counter | channel | Subscriber lost and re-established |
| `redis_pubsub_messages_total` | counter | channel, disposition | received / published / dropped / handler_error |
| `redis_server_*` | gauge | — | memory, clients, evictions, rejected connections |

`redis_circuit_state` is the single most useful of these to alert on, because it
is a **leading** indicator: the breaker opens *before* users notice anything, since
the in-process fallback keeps serving. `redis_up` alone cannot say that — a
process can hold a live connection and still be degraded.

The `redis_*` gauges are read from in-process counters at render time. The
`redis_server_*` gauges come from a background `INFO` sample every
`REDIS_STATS_INTERVAL_SECONDS` (default 30) — **never at scrape time**, following
the same rule as `dependency_up`: whoever can reach `/api/metrics` must not be able
to drive load onto a dependency by scraping faster.

#### Subsystem, MongoDB, WebSocket, tasks, providers, AI, frontend (PH3.7)

Full rationale for each family — why the labels are what they are, and what a
non-zero value actually means — is in
[OBSERVABILITY.md §4](../architecture/OBSERVABILITY.md#4-metrics).

| Metric | Type | Labels | Signal |
|---|---|---|---|
| `subsystem_errors_total` | counter | subsystem, error_class | **Start here.** Which part is failing, and how |
| `auth_events_total` | counter | event, outcome | Login/registration/session outcomes; no identity |
| `mongodb_commands_total` | counter | command, outcome | Every driver command, via pymongo hooks |
| `mongodb_command_duration_seconds` | histogram | command | Buckets start at 250 µs |
| `mongodb_command_errors_total` | counter | command, reason | `reason` is the error *code* name, never a message |
| `mongodb_pool_connections` | gauge | state | `checked_out` near `max` = saturation with **no errors** |
| `websocket_connections_total` | counter | outcome | accepted / rejected |
| `websocket_disconnects_total` | counter | reason | client / error / reaped — abnormal vs normal |
| `websocket_broadcasts_total` | counter | kind | broadcast / channel / user. **Once per fan-out** |
| `websocket_send_failures_total` | counter | kind | Dead sockets found during a fan-out |
| `background_task_starts_total` | counter | task | Any rise after boot = something restarted |
| `background_task_terminations_total` | counter | task, outcome | completed / failed / cancelled |
| `background_task_duration_seconds` | histogram | task | Short buckets: a crash loop is the thing to see |
| `scheduler_job_runs_total` | counter | job, outcome | executed / error / **missed** — `missed` = the job never ran |
| `scheduler_job_duration_seconds` | histogram | job | A job nearing its own interval is about to start missing runs |
| `event_bus_events_total` | counter | event_type | Domain event throughput |
| `event_bus_handler_failures_total` | counter | event_type | Each one is a lost domain action |
| `provider_requests_total` | counter | provider, operation, outcome | ok / **empty** / error |
| `provider_request_duration_seconds` | histogram | provider, operation | Third-party latency |
| `provider_errors_total` | counter | provider, error_class | |
| `ai_requests_total` | counter | provider, outcome | **`provider="simulated"` rising = canned answers** |
| `ai_request_duration_seconds` | histogram | provider | No model label — see §4.3 of OBSERVABILITY.md |
| `ai_request_errors_total` | counter | provider, error_class | |
| `frontend_errors_total` | counter | kind | Browser crashes: render / chunk_load / uncaught / … |
| `frontend_reports_rejected_total` | counter | reason | A client bug, or someone probing the ingest endpoint |

Three of these deserve singling out, because each covers a failure that leaves
every other panel green:

* **`provider_requests_total{outcome="empty"}`** — the provider answered 200
  with no rows. No error, no status code, stale prices on screen.
* **`ai_requests_total{provider="simulated"}`** — the user got a canned
  response because every real model failed. The request *succeeded*.
* **`frontend_errors_total`** — the browser is showing a blank page. The
  request that served the bundle returned 200 minutes ago.

### The cardinality rule

A series is identified by name **and full label set**; each combination costs
memory here and storage in the scraper. Labelling by raw path would create one
series per trade ID and one per URL a vulnerability scanner invents — unbounded
growth in the app *and* the monitoring backend, which is how a metrics change
takes down the system it was meant to observe.

So:
* labels are **route templates** (`/api/trades/{trade_id}`), never raw paths;
* unmatched requests collapse into a single `<unmatched>` bucket;
* a hard ceiling (`METRICS_MAX_SERIES`, default 500) folds excess combinations
  into `<overflow>`.

**`metrics_series_dropped_total > 0` means instrumentation is mislabelled.** It
is the one metric to alert on at any non-zero value.

PH3.7 added a fourth defence for the subsystem families: **closed vocabularies**.
`subsystem`, `provider`, `error_class` and every outcome/reason/kind label are
validated against frozen sets in `observability/instruments.py` and
`observability/errors.py`. A value outside one produces an
`instrumentation_defect` log line and a single `<unknown>` bucket — never a new
series. This is what makes it safe for a label to be passed as a string by a
call site.

### Latency buckets

`0.005 · 0.01 · 0.025 · 0.05 · 0.1 · 0.25 · 0.5 · 1 · 2.5 · 5 · 10` seconds —
dense where traffic and SLOs live, sparse out to 10s for provider stalls.
Changing them between deploys resets the histogram; do not "just add a bucket"
mid-incident and then trust the graph.

Histograms, not averages: 99 requests at 10ms plus one at 10s averages to 110ms
and every dashboard looks fine while a user sits on a broken page.

### Access control

| Environment | Behaviour |
|---|---|
| development / staging | Open |
| production, `METRICS_TOKEN` set | Requires `Authorization: Bearer <token>` or `X-Metrics-Token` (constant-time compare) |
| production, no token | **403, fail closed** |
| production, `METRICS_ALLOW_UNAUTHENTICATED=1` | Open — only for a private scrape network |

Metrics enumerate every route plus traffic and error volumes: reconnaissance
material, and commercially sensitive.

---

## 5. Structured logging

One JSON object per line to **stdout**. Shipping is the platform's job
(twelve-factor) — that is the seam a collector attaches to without changing this
code.

PH2.6 added optional, opt-in **file sinks** on top of stdout: the same records,
split into five streams by logger name, size-rotated, gzipped and
retention-bounded. Stdout is unchanged and unconditional. The schema below is
identical in both. See [LOGGING.md](LOGGING.md).

Default format: JSON in production, human-readable text elsewhere. Override with
`LOG_FORMAT`.

### Record schema

```json
{"timestamp":"2026-07-22T09:41:07.123+00:00","level":"INFO",
 "logger":"stockassist.access","message":"GET /api/trades/{trade_id} 200 12.40ms",
 "service":"stockassist-backend","environment":"production","version":"1.4.2",
 "request_id":"7a18845cb23c483c83c94c9089744318",
 "method":"GET","path":"/api/trades/68f2a1b4",
 "event":"http_request","route":"/api/trades/{trade_id}",
 "http_path":"/api/trades/68f2a1b4","status_code":200,"duration_ms":12.4,
 "client_ip":"203.0.113.7","user_agent":"Mozilla/5.0 ..."}
```

Always present: `timestamp` (ISO-8601 UTC, ms), `level`, `logger`, `message`,
`service`, `environment`, `version`, `request_id`.
Access lines add: `event`, `method`, `route`, `http_path`, `status_code`,
`duration_ms`, `client_ip`, `user_agent`.
Errors add: `exception.{type,message,stacktrace}`.

Any `logger.x(..., extra={...})` field is emitted as a structured field.

**No call-site changes were needed.** Correlation fields are injected by the
*formatter* from the contextvar, so an untouched `logger.warning()` inside any
of the 12,000 lines of existing service code comes out correlated.

### Access log

One line per request — not two. A "request started" line doubles volume to say
what the completion line implies; `http_requests_in_flight` answers "what is
running right now?" far better than counting unmatched start lines.

Severity follows status: 5xx → ERROR, 4xx → WARNING, else INFO. A log platform's
default "errors" view is then correct with no configuration.

Probe traffic (`/api`, `/api/health/*`, `/api/metrics`) is **not** access-logged
unless `LOG_HEALTH_REQUESTS=1`. A 10s cadence across three replicas is ~26,000
identical lines a day — a bill on per-GB ingestion, and it pushes the lines that
matter out of the retention window. Metrics still count every probe.

### What is never logged

| Never logged | Why |
|---|---|
| Query strings | OAuth `code`/`state`, reset tokens, share keys. Stripped at the middleware boundary. |
| Request/response bodies | Passwords on login; financial and personal data elsewhere. |
| Headers | `Authorization` and `Cookie` are credentials verbatim. |
| Sensitive-named fields | Redacted via `security.audit.redact_fields` — the same list the audit log uses. |

**Constraint for future routes: never accept a credential as a path parameter.**
`http_path` is logged (query string removed), so a token in a path segment would
reach the log. Credentials belong in a POST body — which is where every existing
recovery flow puts them.

`LOG_SCRUB_MESSAGES=1` (default) additionally blanks `token=…`, `password: …`,
`Authorization: Bearer …` shaped values inside free-text messages — defence in
depth over pre-existing f-string logging.

Two field names bypass redaction deliberately (`observability/logging.py`
`_SCHEMA_FIELDS`): the redactor's markers are intentionally broad, and
`status_code` contains the substring `code`, so without the exemption the most
queried field in the access log would read `[REDACTED]`.

---

## 6. Request correlation

Every request gets an ID, available five ways:

1. `X-Request-ID` response header (on **every** response, including errors)
2. `request_id` on every log line for that request
3. `request_id` on `security_audit_logs` records
4. `observability.context.current_request_id()` anywhere in the call tree
5. Readable by browser JavaScript (`X-Request-ID` is in the CORS expose list)

### Trust model

An inbound `X-Request-ID` is **validated, not trusted**: accepted only if it
matches `^[A-Za-z0-9._:-]{8,128}$`, otherwise replaced with a fresh
`uuid4().hex`. The header is attacker-controlled — a newline forges log entries,
a 4KB value bloats every record of the request. Propagation is a convenience for
tracing across a proxy, never a trust boundary.

`contextvars`, not a parameter: an asyncio-native binding that is inherited by
spawned tasks and isolated between concurrent requests. A module global or
`threading.local` would leak one user's ID onto another's log lines under async
interleaving.

Outside a request the value is `"-"` (an explicit marker, not an empty string).

**Note (PH1.10 → PH2.5):** `security.audit` has had a `request_id` field since
PH1.10, but nothing generated one, so it was `None` on every record in practice.
It now reads the context first, falling back to the raw header.

---

## 7. Runtime diagnostics

`GET /api/diagnostics` (gated in production, same token as metrics):

```json
{"service":"stockassist-backend","environment":"production",
 "build":{"version":"1.4.2","revision":"a1b2c3d","build_date":"2026-07-22T10:00:00Z"},
 "process":{"pid":1,"python_version":"3.11.15","platform":"Linux",
            "architecture":"aarch64","workers":"4"},
 "started_at":"2026-07-22T09:00:00+00:00","uptime_seconds":3617.42,
 "dependencies":{"mongodb":"configured","redis":"configured"},
 "lifecycle":"ready","timestamp":"..."}
```

Ends the "which version is actually deployed?" argument in one request, instead
of correlating a deploy log with a CI run with a git tag — three systems, any of
which can be wrong. A repeatedly-resetting `uptime_seconds` is a crash loop, and
is visible here before it shows up in error rates (a container that dies during
startup never serves a failing request to be counted).

`APP_VERSION` / `VCS_REF` / `BUILD_DATE` are promoted from Docker build args to
runtime `ENV` in `backend/Dockerfile`, so the image's OCI labels and the running
process cannot disagree.

**Security line:** diagnostics reports facts about the deployment, never
configuration *values*. `dependencies` is presence-only via the secrets
registry's own accessor — a URL containing a password is never rendered.
`workers` is included because "why is my counter wrong?" is nearly always "there
are four workers and you are talking to one".

### `GET /api/diagnostics/redis` (PH2.7)

Same gate, same redaction rules. Returns `connection` (pool occupancy, circuit
state, last error), `pubsub` (per-channel: connected, reconnects, messages,
handler errors) and `server` (the last background `INFO` sample, with its age).
`?refresh=1` forces a fresh round-trip; it is off by default so a monitor pointed
at this URL cannot generate Redis load by polling it.

The `pubsub` section is the reason this endpoint exists. **A process can pass
every Redis health check while its subscription is dead** — pinging Redis and
being subscribed to it are different facts, and only one of them is visible in
`/api/health/ready`. Full treatment in
[`docs/infrastructure/REDIS.md`](../infrastructure/REDIS.md) §5.

---

## 8. Configuration

All registered in `security.secrets.SECRET_REGISTRY` and reflected in
`backend/.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Root level. Invalid → INFO + stderr warning (never fails boot). |
| `LOG_FORMAT` | json (prod) / text | Output format |
| `LOG_SCRUB_MESSAGES` | `1` | Scrub credential-shaped values in free text |
| `LOG_HEALTH_REQUESTS` | `0` | Access-log probe traffic |
| `METRICS_TOKEN` | — | **Required in production** for metrics/diagnostics |
| `METRICS_ALLOW_UNAUTHENTICATED` | `0` | Serve them unauthenticated (private network only) |
| `METRICS_MAX_SERIES` | `500` | Per-metric cardinality ceiling |
| `HEALTH_PROBE_TIMEOUT_SECONDS` | `2.0` | Per-dependency probe timeout |
| `HEALTH_CACHE_TTL_SECONDS` | `2.0` | Readiness result reuse |
| `APP_VERSION` / `VCS_REF` / `BUILD_DATE` | build args | Build provenance |

Keep `HEALTH_PROBE_TIMEOUT_SECONDS` well below the load balancer's probe timeout.
`HEALTH_CACHE_TTL_SECONDS=0` disables caching — every poller then probes Mongo
directly.

---

## 9. Troubleshooting

### "Which log lines belong to this user's error?"

Ask for the ID from the error toast (or read `X-Request-ID` from the failing
response), then:

```
request_id="7a18845cb23c483c83c94c9089744318"
```

That returns the access line, every application line, and any audit record.

### Readiness returns 503

```bash
curl -s localhost:8000/api/health/ready | jq .
```

`reason` tells you which class of problem:

| `reason` | Meaning | Action |
|---|---|---|
| `startup incomplete` | Still booting | Check startup logs; `/api/health/startup` |
| `shutting down` | Draining after SIGTERM | Expected during a deploy |
| `one or more critical dependencies are unhealthy` | See `checks[]` | Below |

A failing check's `detail` is `timeout after 2s` (hung — network partition) or
an exception class name (refused — dependency down). **In production `detail` is
the class name only** — the full error, with its stack, is in the application log
(`logger=observability.health`), because a Mongo error string embeds the
connection URI, credentials included.

### Liveness passes but readiness fails

Working as designed: the process is healthy, a dependency is not. It stays
running and out of rotation, and returns automatically. Do not restart it.

### Container restart-loops with no useful logs

Almost always a missing/too-short `startupProbe`, with liveness killing the boot.
Check `uptime_seconds` in `/api/diagnostics` — if it never exceeds your liveness
threshold, that is the cause.

### No JSON logs in production

`LOG_FORMAT` may be pinned to `text`, or `APP_ENV` may not be `production`.
The boot line `"Structured logging configured (…)"` reports the effective
settings; `/api/diagnostics` reports the effective environment.

### Metrics returns 403 in production

`METRICS_TOKEN` is not set. Fail-closed is intentional. Set it, or set
`METRICS_ALLOW_UNAUTHENTICATED=1` if the path is unreachable from outside.

### `metrics_series_dropped_total` is non-zero

An instrumentation bug: some label is carrying unbounded values (usually a raw
path or an ID). Find it via `?format=json` — look for `<overflow>` — and fix the
label. Raising `METRICS_MAX_SERIES` treats the symptom.

### A latency spike with no error-rate change

Check `http_requests_in_flight`. If it is climbing while latency looks flat, the
slow requests have not *finished* — they are not in the histogram yet. That is a
stuck dependency, and in-flight is the only metric that shows it early.

---

## 10. Measured overhead

| Measurement | Result |
|---|---|
| Middleware overhead | < 0.1 ms/request (CI ceiling asserted at 2 ms) |
| `/api/health/live` | ~0.76 ms in-process |
| `/api/health/ready` (cached) | ~0.77 ms in-process |
| `/api/metrics` render | ~1.2 ms in-process |
| Module import (boot) | ~0.7 s |

The request path only increments integers; aggregation, rendering, `psutil`
sampling and probe execution all happen at scrape/poll time. Readiness results
are cached (2s) so several independent pollers cannot generate continuous ping
load against MongoDB.

---

## 11. Known limitations

> **Updated by PH3.7 (2026-08-15).** Items 3 and 5 below were rewritten: the
> realtime tier and every other subsystem are now instrumented, and an alert
> catalogue exists. What is still missing is narrower and is stated as such.

1. **Metrics are per-process.** With `WEB_CONCURRENCY > 1`, each worker has its
   own registry and a scraper reaches one at random. Aggregation across workers
   is still open (per-worker scrape targets, or a shared store).
2. **Metrics reset on restart.** Inherent to in-process counters; a scraper
   handles it as a counter reset. `app_start_time_seconds` makes resets visible.
3. **WebSocket *latency* is still not measured.** ~~No WebSocket
   instrumentation.~~ PH3.7 added connection, disconnection (by reason),
   fan-out and send-failure counters, alongside PH3.6's gauges. What remains
   absent is per-message latency: a long-lived connection would poison the HTTP
   histogram, and realtime latency needs its own design.
4. **No tracing.** Request IDs correlate within this service only; a
   cross-service trace context (W3C `traceparent`) is a later concern.
5. **Alerts are specified but nothing delivers them.** ~~No alerting.~~ PH3.7
   defines a full alert catalogue with thresholds, severities, expected
   responses and false-positive analysis in
   [OBSERVABILITY.md §9](../architecture/OBSERVABILITY.md#9-alert-catalogue).
   There is still **no Prometheus server, no Alertmanager, no notification
   channel and no uptime check** — detection remains manual, which dominates
   recovery time. Thresholds are also unvalidated, because no staging baseline
   exists.
6. **Logs are not shipped.** PH2.6 added rotation, retention and optional
   durable local files ([LOGGING.md](LOGGING.md)), but nothing forwards them
   off-host yet.
7. **`/api/admin/system/health` still returns partly fabricated data**
   (`/api/admin/apis/health` reports hard-coded latencies). It is an admin
   dashboard endpoint, out of scope here, and should be re-pointed at the real
   provider metrics PH3.7 added (`provider_requests_total`,
   `provider_request_duration_seconds`) in a later sprint.
8. **No error-tracking service** (Sentry/GlitchTip) for either tier. Backend
   exceptions are logged with tracebacks and frontend errors reach
   `frontend_errors_total` plus a structured log line (PH3.7), but neither is
   grouped or deduplicated across releases.

---

## 12. What comes next

* **PH2.6 — Log Infrastructure. ✅ Done (2026-07-22).** Stream separation,
  rotation, retention, compression, redaction verification and Docker logging
  options. See [LOGGING.md](LOGGING.md).
* **PH2.10 — Centralized Logging.** Attach a collector to stdout (or point a
  shipper at the `backend_logs` volume). The JSON schema in §5 is the contract;
  `request_id`, `route` and `status_code` are the index fields. No application
  change required.
* **PH3.7 — Subsystem instrumentation & alert catalogue. ✅ Done (2026-08-15).**
  Error classification, MongoDB/WebSocket/background-task/provider/AI metrics,
  configuration readiness, frontend observability, and a full alert catalogue
  with thresholds, severities and false-positive analysis. See
  [OBSERVABILITY.md](../architecture/OBSERVABILITY.md) — **its §9 supersedes the
  sketch that used to live here.**
* **Still open — Centralized logging.** Attach a collector to stdout (or point a
  shipper at the `backend_logs` volume). The JSON schema in §5 is the contract;
  `request_id`, `route` and `status_code` are the index fields. No application
  change required.
* **Still open — the alerting stack itself.** Point Prometheus at
  `/api/metrics` (bearer auth via `METRICS_TOKEN`), build Grafana panels from the
  four golden signals, load the rules from
  [OBSERVABILITY.md §9](../architecture/OBSERVABILITY.md#9-alert-catalogue),
  wire Alertmanager to a monitored channel, and add an external uptime check on
  the public URL. **Then re-tune every threshold against two weeks of real
  traffic** — they are engineering estimates, not measurements, because there is
  no staging baseline.
* **Still open — error tracking** (Sentry/GlitchTip) for grouping and
  deduplicating exceptions across releases, both tiers.

---

## 13. Document history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-07-22 | Initial document — PH2.5 Production Monitoring & Observability. |
| 1.1 | 2026-07-23 | PH2.7 — Redis metric families, `/api/diagnostics/redis`. |
| 1.2 | 2026-08-15 | PH3.7 — subsystem/MongoDB/WebSocket/task/provider/AI/frontend metric families (§4); closed-vocabulary cardinality defence; configuration readiness check (§3); limitations 3, 5 and 7 rewritten; the alert sketch in §12 replaced by the full catalogue in [OBSERVABILITY.md §9](../architecture/OBSERVABILITY.md#9-alert-catalogue). |

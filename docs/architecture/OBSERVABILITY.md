# Observability Architecture

**Status:** Current as of PH3.7 (2026-08-15)
**Scope:** The whole observability surface — logging, metrics, health, request
correlation, error classification, frontend telemetry, and the alert catalogue.

> **How this document relates to the others.**
> [`docs/operations/MONITORING.md`](../operations/MONITORING.md) is the
> **operator's** manual: how to reach an endpoint, what a field means, how to
> point Prometheus at it. [`docs/operations/LOGGING.md`](../operations/LOGGING.md)
> is the **log infrastructure** reference: streams, rotation, retention, Docker
> drivers. **This** document is the **architecture and the rules** — why the
> design is shaped this way, what the invariants are, and what you must not
> break when adding to it. Read this before adding an instrument; read the other
> two before running the system.

---

## 1. The questions this exists to answer

Observability is not a dashboard. It is the property that an operator can answer
these eight questions without guessing, at 03:00, from outside the process:

| # | Question | Primary signal |
|---|---|---|
| 1 | Is the system healthy? | `GET /api/health/ready` |
| 2 | Which subsystem is failing? | `subsystem_errors_total{subsystem,error_class}` |
| 3 | When did it begin? | Counter rate change; `app_start_time_seconds` for a restart |
| 4 | How many users/requests are affected? | `http_requests_total`, `websocket_connections` |
| 5 | Application, DB, Redis, WS, provider, AI or infra? | `subsystem` label — question 2 answers this directly |
| 6 | Are latency/error/resource metrics degrading? | The four golden signals + per-subsystem histograms |
| 7 | Can we detect it automatically? | §9, the alert catalogue |
| 8 | Can we investigate from logs and metrics? | `request_id` correlation, §5 |

Question 2 is the load-bearing one, and it is why `subsystem_errors_total`
exists as a single metric that every failure path writes to. Everything else in
this document is detail you reach for *after* it has told you where to look.

---

## 2. Architecture

```
                     ┌──────────────────────────────────────────┐
  HTTP request ──▶   │  ObservabilityMiddleware  (outermost)     │
                     │  • assign/validate X-Request-ID           │
                     │  • bind contextvar for the whole call tree│
                     │  • time, count, one access log line       │
                     └───────────────┬──────────────────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        ▼                            ▼                            ▼
  observability.context      observability.metrics       observability.logging
  (request correlation)      (registry → /api/metrics)   (JSON records → stdout)
        │                            ▲                            │
        │                            │                            ▼
        │              observability.instruments          observability.log_streams
        │              (closed vocabularies,              (5 streams, rotation,
        │               the API call sites use)            retention — PH2.6)
        │                            ▲
        │                            │
        │        ┌───────────────────┼───────────────────┬──────────────┐
        │        │                   │                   │              │
        │   security.audit    mongo_monitor      market gateway    AI providers
        │   (auth events)     (driver hooks)     brokers, news     claude/gemini
        │                                        notifications
        ▼
  observability.errors  ── the closed failure vocabulary every label above uses
```

Two additional entry points sit beside the request path:

* **`observability.health`** — the probe registry and the process lifecycle
  state machine behind `/api/health/{live,ready,startup}`.
* **`observability.routes`** — the operational HTTP surface, including the
  client-error ingest endpoint that gives frontend failures a server-side path.

### 2.1 Layering rules

1. **`observability` may not import application code.** `errors` and `context`
   import nothing from this codebase at all; `metrics` imports only stdlib.
   Instrumentation is a leaf, so anything may depend on it and it may depend on
   nothing — which is what keeps it out of every import cycle.
2. **`metrics` declares; other modules register collectors.** Every metric name
   this process can emit is declared in `observability/metrics.py`, so there is
   one file to grep when a dashboard panel says "no data". The code that *fills*
   a gauge lives with the thing that knows what it means
   (`infrastructure/redis_client.py`, `server.py`).
3. **Call sites use `instruments`, not `metrics`.** The exception is the
   middleware, which owns the four HTTP signals. See §4 for why.

### 2.2 The three inviolable rules

These predate PH3.7 and are restated because every addition must satisfy them:

1. **Observability must never take the application down.** Every probe, counter
   and log call is wrapped so its failure degrades telemetry, never the request.
   A metrics bug that 500s a trading endpoint is strictly worse than no metrics.
2. **Cost is paid at scrape time, not request time.** The request path only
   increments integers. Aggregation, rendering, `psutil` calls and `len()` on
   in-process containers all happen when someone asks.
3. **A secret must never reach a log line, a metric label or a health payload.**
   See §7, which is the enforceable version of this sentence.

---

## 3. Error classification

`observability/errors.py` defines a **closed vocabulary of 13 failure classes**.
It is the shared noun set for metric labels, log fields and alert rules.

| Class | Means | Typically alertable? |
|---|---|---|
| `validation` | Caller sent something malformed | No — a spike is a client bug |
| `authentication` | Caller could not be identified | Spike only (credential stuffing) |
| `authorization` | Identified, not permitted | Spike only |
| `rate_limit` | Ours or a provider's limit refused it | Sustained only |
| `database` | MongoDB unreachable/failing/timing out | **Yes** |
| `cache` | Redis failure | Warning — this app degrades in-process |
| `external_provider` | Market data, broker, news, delivery | Sustained only |
| `ai_provider` | Model provider failure | Sustained only |
| `timeout` | Deadline expiry with no subsystem attached | Sustained only |
| `configuration` | Missing/invalid config; usually a deploy fault | **Yes** |
| `unavailable` | Deliberately not called (unconfigured / circuit open) | Context-dependent |
| `internal` | Our bug. Should be the smallest class | **Yes** |
| `cancelled` | Cooperative cancellation — **never counted as an error** | No |

### Two rules that decide edge cases

**The subsystem wins over the failure mode.** `ServerSelectionTimeoutError` is
both a Mongo error and a timeout; it classifies as `database`. "MongoDB is
unreachable" routes to an owner; "something timed out" routes to nobody.
`errors.is_timeout()` is the escape hatch for code deciding whether to retry.

**Cancellation is classified but not counted.** `CancelledError` inherits from
`BaseException`, so `except Exception` never sees it. A clean shutdown cancels
every in-flight operation, and counting those would make every deploy look like
an incident.

### Adding a class

Don't, unless a real incident could not be described by the existing thirteen.
Adding one widens the label space of every metric carrying `error_class` and
invalidates any alert written as an exhaustive match.
`test_observability_subsystems.py::TestErrorClassification` asserts the count,
so the change is deliberate by construction.

---

## 4. Metrics

### 4.1 The cardinality rule — read this before adding a metric

A metric series is identified by its name **and its full label set**. Every
distinct combination is a separate time series with memory here and storage in
whatever scrapes it. This is the mistake that takes monitoring systems down, and
it takes them down weeks after the change, in production, under load — never
locally.

**Never use as a label:** user id, email, JWT or any token, session id, IP
address, symbol, trade/order id, raw URL or path, arbitrary error message, model
id, or any value that arrived from outside the process.

**Three defences, in depth:**

1. **Route templates, not paths.** `middleware.route_template()` returns
   `/api/trades/{trade_id}`; unmatched requests collapse to `<unmatched>`.
2. **Closed vocabularies.** `instruments.SUBSYSTEMS`, `instruments.PROVIDERS`,
   `errors.ERROR_CLASSES` and the outcome sets in `instruments` are frozen. A
   value outside one produces a loud `instrumentation_defect` log line and a
   single `<unknown>` bucket — never a new series.
3. **A hard ceiling.** `METRICS_MAX_SERIES` (default 500) per metric; everything
   past it folds into `<overflow>` and increments
   `metrics_series_dropped_total`. **Any non-zero value there is an alert** — it
   means the label plumbing has a bug and the metrics are quietly incomplete.

Three deliberately open label sets exist, each bounded upstream rather than
here: `auth_events_total{event}` (bounded by `security.audit._EVENT_REGISTRY`,
which folds unregistered events into one bucket), `background_task_*{task}`
(bounded by the loops the application declares at boot), and
`event_bus_events_total{event_type}` (bounded by string literals at the publish
sites). Freezing these in `instruments` would mean a new background loop is
invisible until someone edits that file — trading a real cardinality risk for a
certain observability gap.

### 4.2 The families

| Family | Metrics | Source |
|---|---|---|
| **HTTP** (golden signals) | `http_requests_total`, `http_request_duration_seconds`, `http_request_errors_total`, `http_requests_in_flight` | `ObservabilityMiddleware` |
| **Keystone** | `subsystem_errors_total{subsystem,error_class}` | `instruments.record_error` |
| **Auth** | `auth_events_total{event,outcome}` | `security.audit.AuditLogger.record` |
| **MongoDB** | `mongodb_commands_total`, `mongodb_command_duration_seconds`, `mongodb_command_errors_total`, `mongodb_pool_connections` | `observability.mongo_monitor` (driver hooks) |
| **Redis** | `redis_*` (PH2.7) — client, pub/sub and server families | `infrastructure.redis_client` |
| **WebSocket** | `websocket_connections`, `websocket_tracked_users`, `websocket_channel_subscriptions` (gauges, PH3.6); `websocket_connections_total`, `websocket_disconnects_total`, `websocket_broadcasts_total`, `websocket_send_failures_total` (counters, PH3.7) | `server.ConnectionManager` |
| **Background tasks** | `background_tasks_running` (gauge); `background_task_starts_total`, `background_task_terminations_total`, `background_task_duration_seconds` | `infrastructure.tasks` |
| **Scheduler (cron)** | `scheduler_job_runs_total{job,outcome}`, `scheduler_job_duration_seconds{job}` | `services.scheduler` APScheduler listener |
| **Event bus** | `event_bus_subscribers` (gauge); `event_bus_events_total`, `event_bus_handler_failures_total` | `services.market_engine.event_bus` |
| **Providers** | `provider_requests_total`, `provider_request_duration_seconds`, `provider_errors_total` | `instruments.track_provider` |
| **AI** | `ai_requests_total`, `ai_request_duration_seconds`, `ai_request_errors_total` | `instruments.track_ai` |
| **Frontend** | `frontend_errors_total{kind}`, `frontend_reports_rejected_total{reason}` | `POST /api/observability/client-errors` |
| **Resource bounds** | `app_cache_entries{cache}`, `process_resident_memory_bytes`, `process_open_fds` | PH3.6 collectors |
| **Self** | `metrics_series_dropped_total`, `log_records_dropped_total` | The registry itself |

### 4.3 Three non-obvious design choices

**`provider_requests_total` has an `empty` outcome.** The market-data failure
that no status code shows: the provider answers 200 with no rows, every
error-rate panel stays green, and the product serves yesterday's prices. Alert
on `empty` as a *share* of the total, not on its absolute value — some
operations are legitimately empty outside market hours.

**`ai_requests_total{provider="simulated"}` is the most important AI series.**
Every path that reaches the simulated provider has already failed to get a real
model, and the user receives a plausible-looking canned response. The request
succeeds, HTTP metrics stay green, nothing is logged as an error. This counter
is the only evidence the product is silently not working.

**Cron is instrumented separately from the perpetual loops, and `missed` is why.**
`background_task_*` covers the loops in `infrastructure/tasks.py`; the six
APScheduler cron jobs have their own family. Conflating them would hide the
failure mode only cron has: **a job that does not run at all**. A perpetual loop
announces its death by stopping; a scheduled run skipped past its misfire grace
period — because the event loop was blocked, or the previous run is still going
— calls nothing, raises nothing, and logs nothing from inside the job, because
the job never executed. Only the scheduler's own event stream knows.
`trade_monitor` fires every 60 s during market hours, so
`scheduler_job_runs_total{outcome="missed"}` means live positions went
unchecked.

**WebSocket fan-out is counted per broadcast, not per recipient.** A broadcast
to 500 sockets four times a second is 2,000 sends; counting each would put 2,000
lock acquisitions per second on the realtime path to learn a number the
connection gauge already implies. A fan-out is two increments regardless of
audience size, with failures added in one sized increment.

### 4.4 Adding an instrument

1. Declare the metric in `observability/metrics.py`, with a `HELP` string that
   says what a non-zero value *means*, not what the metric is named.
2. Add a helper to `observability/instruments.py` that updates the whole family
   together, wrapped so it cannot raise.
3. If it takes a label, either add the values to a frozen set or document why
   the set is bounded upstream.
4. Add a test to `tests/test_observability_subsystems.py`, including a
   redaction sweep if the helper accepts free text.

---

## 5. Structured logging and request correlation

Both were delivered in PH2.5/PH2.6 and are unchanged by PH3.7. Summarised here
because the rules bind new code.

**Every log record carries** `timestamp`, `level`, `logger`, `service`,
`environment`, `version`, `request_id`, and — on access records — `method`,
`route`, `status_code`, `duration_ms`, `client_ip`. Production emits JSON to
stdout unconditionally (12-factor: shipping is the platform's job); development
may use the human formatter.

**Request correlation.** `ObservabilityMiddleware` accepts an inbound
`X-Request-ID` **only after validation** (8–128 chars, `[A-Za-z0-9._:-]`) and
otherwise mints a `uuid4().hex`. An unvalidated inbound ID is log injection: a
newline forges entries and an unbounded value bloats every record. The ID is
bound to a `contextvar` for the whole call tree, stamped on every response, and
attached to every audit record.

**Query strings are discarded at the middleware boundary** and never logged. In
this application a query string can carry a Google OAuth `code`, a broker
callback token or a password-recovery token.

**Five streams** by logger name — application / access / security / audit /
error — so retention can differ per stream. File sinks are opt-in
(`LOG_TO_FILES`) and sit behind a bounded `QueueListener`, so disk I/O and
rotation never touch the event loop.

### High-frequency events

**Never log per market tick.** Price updates, broadcast sends and cache reads
are counted, not narrated. The rule for a high-frequency path is: a counter
always, a log line only on failure, and never a log line per item in a fan-out.
Health-probe access lines are suppressed by default
(`LOG_HEALTH_REQUESTS=1` re-enables them); their metrics are still recorded.

---

## 6. Health and readiness

| Endpoint | Question | Dependencies checked | Fails when |
|---|---|---|---|
| `/api/health/live` | Should this container be restarted? | **None, by design** | The process cannot respond at all |
| `/api/health/ready` | Should it receive traffic? | MongoDB (critical), configuration (critical), Redis (non-critical) | Any *critical* check fails |
| `/api/health/startup` | Has it finished booting? | Lifecycle state | Still starting |
| `/api/health` | Human summary | All | — |

**Liveness depends on nothing external.** A liveness probe that checks MongoDB
turns a database blip into a rolling restart of every application container —
removing the only thing that was still working.

**Redis is non-critical.** `services/cache.py` falls back to an in-process dict,
so a single-process deployment without Redis is supported, not faulty. The probe
returns `skip` when `REDIS_URL` is unset (surfaced as `dependency_up = -1`, which
an alert rule must distinguish from `0`). Promote it to critical when Redis
becomes load-bearing for multi-process fan-out.

**Configuration is critical (PH3.7).** It reads the verdict `validate_config()`
already produced at boot — an attribute access, not a re-validation, because
readiness is polled every few seconds by every replica. The failure detail is a
*count* of failing names, never the names: an unauthenticated caller learning
which secret a deployment is missing is a reconnaissance gift.

**Probes run in parallel, under timeout, behind a short result cache**, so a
readiness poll cannot generate load proportional to its frequency.

**Nothing sensitive is returned.** In production `_safe_detail()` emits the
exception *class name* only —
`ServerSelectionTimeoutError` stringifies to a message embedding the full Mongo
URI, credentials included, and readiness is reachable by anything that can reach
the service.

---

## 7. Sensitive data: the rules and where they are enforced

**Never recorded anywhere** — logs, metrics, health payloads or diagnostics:
passwords and hashes, JWTs, refresh/access tokens, OAuth authorization codes,
API keys, recovery tokens, session ids, connection URIs with credentials, and
prompt or response content.

| Rule | Enforced by | Test |
|---|---|---|
| Query strings never logged | Discarded in `middleware.__call__` | `test_observability.py::test_query_strings_are_never_logged` |
| Audit metadata redacted | `security.audit._redact` marker list | `test_audit.py` |
| Free-text log messages scrubbed | `observability.logging.scrub_message` | `test_observability.py` |
| Mongo `errmsg` never labelled | `mongo_monitor._failure_reason` uses the integer code only | `test_a_server_error_message_never_becomes_a_label` |
| AI error strings never labelled | `instruments._classify_ai_error_text` returns a class, not the text | `test_an_error_string_is_classified_but_never_recorded` |
| Exception messages never labelled | `errors.classify_exception` returns a class | `test_provider_exception_messages_never_reach_the_document` |
| Closed vocabularies actually refuse | `instruments._bounded` / `_subsystem` / `_provider` | `test_a_closed_vocabulary_label_cannot_carry_an_arbitrary_value` |
| Health details safe in production | `health._safe_detail` | `test_observability.py` |
| Client reports clipped and de-newlined | `routes._clip` | `test_newlines_are_stripped_so_a_report_cannot_forge_a_log_line` |
| Frontend collects no PII | `services/telemetry.js` reads no storage, sends pathname only | `telemetry.test.js` — "what is never collected" |

The metrics regression sweeps are the important ones: they drive every free-text
path with secret-shaped input and assert the strings are **absent** from the
rendered exposition document. "We were careful" is not a property a test can
check; "this string does not appear" is.

### Access control

`/api/health/*` is public — infrastructure probes hold no credentials, and
authenticating them creates a new way for the deployment to break. Its payload
is correspondingly minimal.

`/api/metrics` and `/api/diagnostics*` are **token-gated in production**
(`METRICS_TOKEN`, as `Authorization: Bearer` or `X-Metrics-Token`). With no token
configured they **fail closed with 403** — defaulting to open is how metrics
endpoints end up indexed by Shodan. `METRICS_ALLOW_UNAUTHENTICATED=1` is the
explicit opt-out for a private scrape network.

`POST /api/observability/client-errors` is **unauthenticated by necessity** —
the failures most worth hearing about are the ones where the app could not
start, where a chunk failed to load, or where the auth provider itself threw,
none of which have a usable credential. It is therefore treated as hostile
input: a closed `kind` vocabulary, hard field caps, newline stripping, no
database write, CSRF-exempt (it changes no state and is delivered by
`sendBeacon`, which cannot set a header), and covered by the platform per-IP
rate limiter.

---

## 8. Frontend observability

Before PH3.7 the frontend had **no error boundary and no global handlers**. A
React render error unmounted the entire tree — a white page — with the cause
visible only in a console the user would never open, and no server-side trace of
any kind.

| Failure | Caught by | Reported as |
|---|---|---|
| Render / lifecycle error | `components/ErrorBoundary.jsx` | `render` |
| Stale bundle after a deploy | `ErrorBoundary` (chunk detection) | `chunk_load` |
| Unhandled promise rejection | `services/telemetry.js` window handler | `unhandled_rejection` |
| Uncaught error (handler, timer) | `services/telemetry.js` window handler | `uncaught` |
| Failed API call the UI could not handle | `reportClientError` at the call site | `api` |
| WebSocket failure | `reportClientError` at the call site | `websocket` |

**Two levels of boundary.** The outer one wraps the providers in `App.js` (so a
throw inside `AuthProvider` is caught) and shows a full-page recovery screen.
The inner ones wrap `<Outlet/>` in both layouts, keyed by pathname, so a page
crash keeps the sidebar and navbar and the user has a route out that is not the
back button.

**Chunk-load auto-recovery is bounded to one attempt per tab**
(`sessionStorage`). If a reload does not fix it, reloading again is an infinite
refresh against a failing origin from every affected browser at once.

**In production the boundary shows no error message and no stack.** A React
error message can quote component props, which in this application means
positions, prices and account values — a screenshot of a crash would be a leak.

**Client-side rate limiting comes first.** A hard cap of 20 reports per page
session plus deduplication by signature, applied before anything is sent. A
render loop throws thousands of times a second, and an error inside a reporting
path is the classic way to turn one bug into a self-inflicted denial of service.

**No third-party telemetry service.** No Sentry, no session replay, no
analytics. Reports go to this application's own endpoint and become a counter
and a log line.

---

## 9. Alert catalogue

Deliberately short. Every alert that fires without a human action attached
trains people to ignore the pager, which is worse than having no alert. These
are the conditions worth waking someone for, plus a small warning tier that
belongs on a dashboard and in a daily review.

Thresholds are **starting points**, not measurements: this application has never
run in a durable staging environment (roadmap PH2.12), so no baseline exists for
normal traffic. Every `for` duration and every rate below must be re-tuned
against the first two weeks of production data. **Tuning them is a required
pre-production task, tracked in §12.**

### Critical — page immediately

| # | Signal | Condition | Why | False positives |
|---|---|---|---|---|
| A1 | Readiness failing | `up == 0` **or** `/api/health/ready` != 200 for **2m** on ≥1 instance | Instance is serving errors or is out of rotation | Deploys — exclude during a rollout window |
| A2 | MongoDB unavailable | `dependency_up{dependency="mongodb"} == 0` for **1m** | No critical path works without it | A failover is a legitimate brief blip; 1m rides it out |
| A3 | 5xx spike | `rate(http_request_errors_total{kind="server"}[5m]) / rate(http_requests_total[5m]) > 0.05` for **5m** | Users are seeing failures | Low-traffic periods make the ratio jumpy — pair with `rate(http_requests_total[5m]) > 0.1` |
| A4 | Unhandled exceptions | `rate(http_request_errors_total{kind="exception"}[5m]) > 0` for **5m** | A handler is crashing, not returning an error — always a bug | None; any sustained value is real |
| A5 | Configuration invalid | `dependency_up{dependency="configuration"} == 0` | A bad deploy; the instance should not be serving | None — this cannot happen on a healthy boot |
| A6 | Crash loop | `app_uptime_seconds < 300` for **10m** | The process is restarting repeatedly | A rolling deploy; suppress during rollouts |

### Warning — investigate within a business day

| # | Signal | Condition | Why | False positives |
|---|---|---|---|---|
| B1 | Instrumentation bug | `metrics_series_dropped_total > 0` | The label plumbing is broken and metrics are silently incomplete | None. Any non-zero value is a defect |
| B2 | Redis degraded | `redis_circuit_state == 2` for **5m** | Serving from the in-process fallback — correct but slower and per-process | A deliberate Redis restart |
| B3 | Auth-failure spike | `rate(auth_events_total{outcome="failure"}[5m]) > 5×` the 1h baseline | Credential stuffing or a broken client | A broken mobile client retrying looks identical — check the event mix |
| B4 | Token replay | `increase(auth_events_total{event="token_replay_detected"}[15m]) > 0` | A stolen refresh token was reused | Near zero. Treat every occurrence as real |
| B5 | Abnormal WS disconnects | `rate(websocket_disconnects_total{reason=~"error\|reaped"}[5m]) > rate(websocket_disconnects_total{reason="client"}[5m])` for **10m** | Network or backpressure problem, not users navigating | Mobile networks are genuinely lossy — tune the ratio |
| B6 | Provider failing | `rate(provider_errors_total[10m]) / rate(provider_requests_total[10m]) > 0.25` for **10m**, by provider | Degraded data; users see stale prices | Providers rate-limit outside market hours |
| B7 | Provider silently empty | `rate(provider_requests_total{outcome="empty"}[15m]) / rate(provider_requests_total[15m]) > 0.5` for **15m** | 200-with-no-rows: the failure every error-rate panel misses | Legitimately empty outside market hours — **gate on market hours** |
| B8 | AI degraded to simulated | `increase(ai_requests_total{provider="simulated"}[15m]) > 0` | Users are getting canned answers; everything else looks green | Expected in an unconfigured environment — alert only where a key is configured |
| B9 | Background task crashing | `increase(background_task_terminations_total{outcome="failed"}[1h]) > 0` | A perpetual loop's own try/except failed — always a defect | None |
| B10 | Task restart churn | `increase(background_task_starts_total[1h]) > 1` per task, without a restart | Something is respawning loops | Restarts and deploys |
| B10a | Cron job failing | `increase(scheduler_job_runs_total{outcome="error"}[1h]) > 0` | A scheduled job raised — a missed report, an unmonitored position | None |
| B10b | Cron job **not running** | `increase(scheduler_job_runs_total{outcome="missed"}[30m]) > 0` | The run was skipped entirely; nothing inside the job can report this | None. A blocked loop or an overrunning previous run — both real |
| B10c | Cron job overrunning | `histogram_quantile(0.95, rate(scheduler_job_duration_seconds_bucket{job="trade_monitor"}[1h])) > 45` | Approaching its own 60 s interval; misses come next | A slow provider day — pair with B6 |
| B11 | Mongo pool saturated | `mongodb_pool_connections{state="checked_out"} / mongodb_pool_connections{state="max"} > 0.8` for **10m** | Pool exhaustion presents as uniform slowness with **no errors at all** | A genuine traffic spike |
| B12 | Latency SLO | `histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m])) > 1` for **10m** | Users are waiting | A slow report endpoint skews the aggregate — alert per route where it matters |
| B13 | Resource ratchet | `app_cache_entries` or `websocket_tracked_users` rising monotonically over **6h** | The PH3.6 leak signature | Genuine growth in active users |
| B14 | The PH3.6 signature | `websocket_tracked_users > 0` **while** `websocket_connections == 0` for **10m** | Per-user entries retained after disconnect — the exact bug PH3.6 fixed | None. This state is never legitimate |
| B15 | Frontend crash spike | `rate(frontend_errors_total{kind="render"}[10m])` above baseline | A shipped render bug | A single user in a reload loop — the client cap bounds it to 20 |
| B16 | Stale bundles | `increase(frontend_errors_total{kind="chunk_load"}[15m])` elevated | Users on a stale `index.html`; a deploy signature, not a regression | **Expected for ~15m after every deploy** — suppress during that window |
| B17 | Event handlers failing | `rate(event_bus_handler_failures_total[10m]) > 0` for **10m** | Each one is a domain action that silently did not happen | None |
| B18 | Log pipeline broken | `log_records_dropped_total > 0` | Log files on that instance have gaps | A burst under extreme load |
| B19 | Ingest probing | `rate(frontend_reports_rejected_total[10m])` elevated | A client bug, or someone probing the endpoint | A stale frontend build |

### Not alerts

`validation`, `authentication` and `authorization` errors at a *steady* rate are
the internet, not an incident. `dependency_up == -1` means "not configured", not
"down" — an alert rule that treats it as a failure will page on every
development deployment.

### What still has no delivery channel

**Nothing watches any of this yet.** There is no Prometheus server, no
Alertmanager, no notification channel, no uptime check on the public URL.
Detection is manual, which dominates recovery time. That gap is roadmap PH2.10's
remaining scope, restated in §12.

---

## 10. Overhead

**Measured**, not estimated — Apple M-series, Python 3.11, median of five runs
of 20,000 calls each after a 5,000-call warmup. The absolute numbers are
hardware-bound; the ratios in the second table are what transfer.

| Path | Measured | Notes |
|---|---|---|
| `Counter.inc` (labelled) | **0.52 µs** | One lock acquire + dict lookup + add |
| `Gauge.set` | **0.41 µs** | |
| `Histogram.observe` (11 buckets) | **0.98 µs** | Adds a `bisect` and a bucket walk |
| `record_error` (keystone) | **0.63 µs** | The metric every failure path writes to |
| `classify_exception` | **0.92 µs** | MRO walk against a dict — no imports, no isinstance |
| `record_exception` (classify + record) | **1.61 µs** | |
| `record_auth_event` | **0.61 µs** | Per audit event |
| `record_ws_fanout` (0 failures) | **0.59 µs** | **Per fan-out, not per recipient** |
| `record_ws_fanout` (3 failures) | **2.17 µs** | Constant in audience size |
| `record_mongo_command` | **1.72 µs** | |
| `CommandMetricsListener.succeeded` | **2.03 µs** | The always-on one, per Mongo command |
| `PoolMetricsListener` checkout | **1.14 µs** | |
| `track_provider` (context manager) | **2.12 µs** | |
| `track_ai` (context manager) | **1.94 µs** | |
| `render_prometheus` (baseline, ~10 KB) | **0.76 ms** | Paid by the scraper |
| `render_prometheus` (+500 series, ~50 KB) | **0.61 ms** | Sub-linear; the ceiling bounds it |

**The ratios that decide whether any of this matters:**

| Instrumented path | Typical work | Overhead |
|---|---|---|
| Mongo command | 1–10 ms | **~0.02–0.2%** |
| HTTP request | 5–15 ms | **<0.05%** (~2–4 µs of new work on top of PH2.5's middleware) |
| Provider call | 100 ms–5 s | **~0.002%** |
| AI call | 1–30 s | immeasurable |
| WebSocket fan-out to 500 sockets | ~5–15 ms of `send_text` | **~0.6 µs total** |

The last row is the one the design turned on. Counting per recipient would have
made it ~260 µs (500 × 0.52) at four broadcasts a second — still small, but
growing with every connected user, on the one path in this application where
that scaling is unacceptable. Counted per fan-out it is constant.

**What is not measured, and should not be read as if it were.** These are
microbenchmarks of individual calls, not p99 under concurrency. There is no
load-generated figure because there is no durable staging environment (roadmap
PH2.12), and lock contention under real concurrency is precisely what a
single-threaded loop cannot show. Re-measure with k6 (`scripts/load/`) against
staging when it exists; §12.11 tracks this.

**Escape hatches.** `MONGO_COMMAND_METRICS=0` disables driver instrumentation.
`METRICS_MAX_SERIES` bounds memory. `LOG_LEVEL` and `LOG_HEALTH_REQUESTS` bound
log volume. `LOG_TO_FILES` is off by default.

Reproduce these figures with `backend/scripts/observability_overhead.py`.

---

## 11. Troubleshooting flow

Start at the top; each step narrows the next.

1. **`GET /api/health/ready`** — which dependency, and is it critical?
2. **`subsystem_errors_total`, grouped by `subsystem`** — which part is failing?
3. **Same metric, grouped by `error_class`** — how is it failing? That decides
   whether this is ours (`internal`, `configuration`) or theirs
   (`external_provider`, `ai_provider`, `rate_limit`).
4. **The subsystem's own family** — `mongodb_*`, `redis_*`, `provider_*`,
   `ai_*`, `websocket_*` — for latency and specific failure reasons.
5. **`app_uptime_seconds` and `app_info`** — did this start at a deploy? A
   `configuration` class plus a fresh uptime is a bad release, and the whole
   diagnosis.
6. **Logs, filtered by `error_class` and the subsystem's logger.** Then take a
   `request_id` from any one line and re-filter on it alone: that returns the
   entire causal chain for one user action across middleware, services and
   repositories.
7. **`GET /api/diagnostics`** (and `/api/diagnostics/redis`) for build
   provenance and the full Redis picture.

**Common shapes:**

* Latency up across every route, no errors → `mongodb_pool_connections` (B11) or
  `redis_circuit_state` (B2).
* Everything green but users complain about data → `provider_requests_total{outcome="empty"}` (B7)
  or `ai_requests_total{provider="simulated"}` (B8).
* Everything green but users see a blank page → `frontend_errors_total`.
* A gauge that only ever rises → B13/B14; run `backend/scripts/resource_probe.py`.
* A metric panel reads "no data" → grep the name in `observability/metrics.py`;
  if it exists, its collector is not registered.

---

## 12. Known limitations and what is still owed

Stated plainly, because a limitation an operator discovers during an incident is
worse than one they read about beforehand.

1. **No alert delivery.** No Prometheus, no Alertmanager, no channel, no uptime
   check. §9 is a specification, not a running system. Detection is manual and
   dominates RTO. *(Roadmap PH2.10.)*
2. **Thresholds are unvalidated.** No staging environment means no baseline.
   Every number in §9 is an engineering estimate. *(Roadmap PH2.12.)*
3. **Metrics are per-process.** With `WEB_CONCURRENCY > 1` each worker has its
   own registry and a scraper reaches one at random. Needs per-worker scrape
   targets or a shared store.
4. **Metrics reset on restart.** Inherent to in-process counters; a scraper
   handles it as a counter reset, and `app_start_time_seconds` makes it visible.
5. **No distributed tracing.** Request IDs correlate within this service only.
   W3C `traceparent` is a later concern — the ID format already accommodates it.
6. **No error-tracking service.** No Sentry/GlitchTip for either tier. Backend
   exceptions are logged with tracebacks; frontend errors reach the counter and
   the log. Neither is grouped or deduplicated across releases.
7. **Logs are not shipped.** Rotation and retention exist (PH2.6); nothing
   forwards them off-host. The JSON schema is the contract when something does.
8. **WebSocket latency is not measured.** Long-lived connections would poison
   the HTTP histogram; realtime latency needs its own design.
9. **No AI cost or token metrics.** Deliberate — a per-user token count is
   business data on an operational endpoint. Cost tracking belongs with billing.
10. **`/api/admin/apis/health` still returns hard-coded latencies.** An admin
    dashboard endpoint predating this work; it should be re-pointed at the real
    provider metrics. Out of scope here, recorded as debt.
11. **Overhead is unmeasured under load.** §10 is microbenchmarks. Re-measure
    with k6 once staging exists.

---

## 13. Document history

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-08-15 | Initial — PH3.7 Monitoring & Observability. Consolidates the PH2.5/PH2.6/PH2.7/PH3.6 architecture and adds error classification, subsystem instrumentation, MongoDB/WebSocket/task/provider/AI metrics, configuration readiness, frontend observability, and the alert catalogue. |
